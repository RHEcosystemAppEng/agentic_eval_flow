"""Analyze Harbor A/B evaluation results and produce JSON + Markdown reports.

Walks the result directory tree produced by ``harbor-eval`` (two separate
Harbor jobs, one per variant), computes per-variant statistics, runs
statistical significance tests, and writes a structured report.

Expected input layout::

    <results-dir>/
        treatment/
            <job-name>/
                <task>__<uuid>/result.json
                ...
        control/
            <job-name>/
                <task>__<uuid>/result.json
                ...

Usage::

    python scripts/analyze.py \\
        --results-dir /workspace/eval-results/my-submission \\
        --output-dir /workspace/reports/my-submission \\
        --submission-name my-submission \\
        --threshold 0.0
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path
from statistics import median, stdev
from typing import Any

from scipy import stats as sp_stats

from abevalflow.report import (
    AnalysisResult,
    AnalysisSummary,
    DegradationResult,
    Provenance,
    Recommendation,
    ScanMode,
    SecurityFinding,
    SecurityScanResult,
    SecuritySeverity,
    TrialResult,
    VariantSummary,
)

logger = logging.getLogger(__name__)

VARIANTS = ("treatment", "control")


# ---------------------------------------------------------------------------
# Security scan parsing
# ---------------------------------------------------------------------------


def parse_security_scan(report_dir: Path, scan_mode: str | None) -> SecurityScanResult | None:
    """Parse security-scan.json if it exists in the report directory.

    Returns None if scanning was disabled or file doesn't exist.
    """
    scan_json = report_dir / "security-scan.json"
    if not scan_json.exists():
        logger.debug("No security-scan.json found, security scanning was disabled")
        return None

    if not scan_mode:
        scan_mode = "warn"

    try:
        data = json.loads(scan_json.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to parse security-scan.json: %s", e)
        return None

    findings: list[SecurityFinding] = []
    for f in data.get("findings", []):
        try:
            sev_str = f.get("severity", "info").lower()
            severity = (
                SecuritySeverity(sev_str) if sev_str in SecuritySeverity.__members__.values() else SecuritySeverity.INFO
            )
            findings.append(
                SecurityFinding(
                    rule_id=f.get("rule_id", f.get("id", "unknown")),
                    severity=severity,
                    message=f.get("message", f.get("description", "")),
                    file_path=f.get("file_path", f.get("location", {}).get("file")),
                    line_number=f.get("line_number", f.get("location", {}).get("line")),
                    scanner="cisco",
                )
            )
        except Exception as e:
            logger.warning("Failed to parse finding: %s", e)

    high_or_critical = sum(1 for f in findings if f.severity in (SecuritySeverity.CRITICAL, SecuritySeverity.HIGH))
    passed = scan_mode != "block" or high_or_critical == 0

    return SecurityScanResult(
        scanner="cisco",
        scan_mode=ScanMode(scan_mode),
        findings=findings,
        passed=passed,
    )


# ---------------------------------------------------------------------------
# Result parsing
# ---------------------------------------------------------------------------


def _extract_reward(result: dict) -> float | None:
    """Extract reward from a Harbor result.json.

    Handles two known formats:
    - Nested: ``verifier_result.rewards.reward`` (actual Harbor output)
    - Flat:   ``verifier_result.reward`` (used in harbor-eval inline parser)
    """
    vr = result.get("verifier_result")
    if not isinstance(vr, dict):
        return None
    rewards = vr.get("rewards")
    if isinstance(rewards, dict):
        r = rewards.get("reward")
        if r is not None:
            return float(r)
    r = vr.get("reward")
    if r is not None:
        return float(r)
    return None


def parse_variant_trials(variant_dir: Path) -> list[TrialResult]:
    """Scan all trial directories under a variant's results directory.

    Skips job-level ``result.json`` files (which lack ``verifier_result``)
    so only actual trial results are counted.
    """
    trials: list[TrialResult] = []
    if not variant_dir.is_dir():
        return trials

    for result_file in sorted(variant_dir.rglob("result.json")):
        trial_name = result_file.parent.name
        try:
            data = json.loads(result_file.read_text())
            if "verifier_result" not in data:
                continue
            reward = _extract_reward(data)
            trials.append(TrialResult(trial_name=trial_name, reward=reward))
        except (json.JSONDecodeError, ValueError, TypeError):
            trials.append(TrialResult(trial_name=trial_name))

    return trials


def is_a2a_results(results_dir: Path) -> bool:
    """Return True when results use the flat A2A layout (a2a-eval/ only)."""
    a2a_dir = results_dir / "a2a-eval"
    if not a2a_dir.is_dir():
        return False
    has_ab_variants = (results_dir / "treatment").is_dir() or (results_dir / "control").is_dir()
    return not has_ab_variants


def parse_a2a_trials(a2a_dir: Path) -> list[TrialResult]:
    """Parse trial result.json files from the flat a2a-eval/ directory."""
    return parse_variant_trials(a2a_dir)


def build_a2a_analysis(
    results_dir: Path,
    submission_name: str,
    threshold: float = 0.0,
    provenance: Provenance | None = None,
    related_pr: str | None = None,
    llm_label: str | None = None,
) -> AnalysisResult:
    """Parse A2A single-variant results and assemble an analysis model."""
    a2a_trials = parse_a2a_trials(results_dir / "a2a-eval")
    summary = compute_variant_summary(a2a_trials)
    empty_control = VariantSummary()

    _MIN_TRIALS_FOR_RELIABLE_STATS = 15
    if 0 < summary.n_trials < _MIN_TRIALS_FOR_RELIABLE_STATS:
        logger.warning(
            "A2A has only %d trials (< %d) — statistics may be unreliable",
            summary.n_trials,
            _MIN_TRIALS_FOR_RELIABLE_STATS,
        )

    if summary.n_trials == 0:
        logger.warning("No A2A trial data — defaulting to FAIL")
        recommendation = Recommendation.FAIL
    elif summary.n_passed == 0:
        logger.warning("Zero passes in A2A evaluation — defaulting to FAIL")
        recommendation = Recommendation.FAIL
    else:
        pass_threshold = threshold if threshold > 0 else 0.5
        mean_r = summary.mean_reward or 0.0
        recommendation = Recommendation.PASS if mean_r >= pass_threshold else Recommendation.FAIL

    return AnalysisResult(
        submission_name=submission_name,
        provenance=provenance or Provenance(),
        summary=AnalysisSummary(
            related_pr=related_pr,
            llm=llm_label,
            treatment=summary,
            control=empty_control,
            uplift=summary.pass_rate,
            mean_reward_gap=summary.mean_reward,
            ttest_p_value=None,
            fisher_p_value=None,
            recommendation=recommendation,
        ),
        trials={"treatment": a2a_trials, "control": []},
    )


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def compute_variant_summary(trials: list[TrialResult]) -> VariantSummary:
    """Compute aggregate stats from a list of trial results."""
    rewards = [t.reward for t in trials if t.reward is not None]
    n_errors = sum(1 for t in trials if t.reward is None)
    n_passed = sum(1 for t in trials if t.passed)
    n_total = len(trials)
    n_failed = n_total - n_passed - n_errors

    pass_rate = n_passed / n_total if n_total > 0 else 0.0
    mean_r = sum(rewards) / len(rewards) if rewards else None
    median_r = median(rewards) if rewards else None
    std_r = stdev(rewards) if len(rewards) > 1 else None

    return VariantSummary(
        n_trials=n_total,
        n_passed=n_passed,
        n_failed=n_failed,
        n_errors=n_errors,
        pass_rate=pass_rate,
        mean_reward=mean_r,
        median_reward=median_r,
        std_reward=std_r,
    )


def compute_ttest(treatment_trials: list[TrialResult], control_trials: list[TrialResult]) -> float | None:
    """Welch's t-test on continuous reward scores between variants."""
    t_rewards = [t.reward for t in treatment_trials if t.reward is not None]
    c_rewards = [t.reward for t in control_trials if t.reward is not None]
    if len(t_rewards) < 2 or len(c_rewards) < 2:
        return None
    _, p = sp_stats.ttest_ind(t_rewards, c_rewards, equal_var=False)
    if math.isnan(p):
        return None
    return float(p)


def compute_fisher(treatment_summary: VariantSummary, control_summary: VariantSummary) -> float | None:
    """Fisher's exact test on the 2x2 pass/fail contingency table.

    Error trials (missing/corrupt results) are excluded from the table so
    infrastructure failures don't skew significance.
    """
    t_pass = treatment_summary.n_passed
    t_fail = treatment_summary.n_failed
    c_pass = control_summary.n_passed
    c_fail = control_summary.n_failed
    if (t_pass + t_fail) == 0 or (c_pass + c_fail) == 0:
        return None
    table = [[t_pass, t_fail], [c_pass, c_fail]]
    _, p = sp_stats.fisher_exact(table)
    return float(p)


# ---------------------------------------------------------------------------
# Degradation merge
# ---------------------------------------------------------------------------


def degradation_from_monitor(monitor_data: dict) -> DegradationResult:
    """Build a DegradationResult from monitor.py JSON output."""
    return DegradationResult(
        degraded=monitor_data["degraded"],
        message=monitor_data["message"],
        threshold=monitor_data.get("threshold"),
        previous_pass_rate=monitor_data.get("previous_score"),
        current_pass_rate=monitor_data.get("current_score"),
    )


def merge_degradation_into_report(
    report_path: Path,
    monitor_path: Path,
) -> AnalysisResult:
    """Merge degradation check results from monitor.py into an existing report.json."""
    monitor_data = json.loads(monitor_path.read_text())
    result = AnalysisResult.model_validate_json(report_path.read_text())
    result.degradation = degradation_from_monitor(monitor_data)
    report_path.write_text(result.model_dump_json(indent=2))
    logger.info("Merged degradation results into %s", report_path)

    md_path = report_path.parent / "report.md"
    md_path.write_text(render_markdown(result))
    logger.info("Regenerated Markdown report at %s", md_path)

    return result


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------


def build_analysis(
    results_dir: Path,
    submission_name: str,
    threshold: float = 0.0,
    provenance: Provenance | None = None,
    related_pr: str | None = None,
    llm_label: str | None = None,
    eval_engine: str | None = None,
) -> AnalysisResult:
    """Parse results, compute stats, and assemble the full analysis model."""
    if eval_engine == "a2a" or is_a2a_results(results_dir):
        return build_a2a_analysis(
            results_dir=results_dir,
            submission_name=submission_name,
            threshold=threshold,
            provenance=provenance,
            related_pr=related_pr,
            llm_label=llm_label,
        )

    treatment_trials = parse_variant_trials(results_dir / "treatment")
    control_trials = parse_variant_trials(results_dir / "control")

    t_summary = compute_variant_summary(treatment_trials)
    c_summary = compute_variant_summary(control_trials)

    _MIN_TRIALS_FOR_RELIABLE_STATS = 15
    for label, vs in [("treatment", t_summary), ("control", c_summary)]:
        if 0 < vs.n_trials < _MIN_TRIALS_FOR_RELIABLE_STATS:
            logger.warning(
                "%s has only %d trials (< %d) — statistical tests may be unreliable",
                label,
                vs.n_trials,
                _MIN_TRIALS_FOR_RELIABLE_STATS,
            )

    uplift = t_summary.pass_rate - c_summary.pass_rate
    mean_gap = None
    if t_summary.mean_reward is not None and c_summary.mean_reward is not None:
        mean_gap = t_summary.mean_reward - c_summary.mean_reward

    ttest_p = compute_ttest(treatment_trials, control_trials)
    fisher_p = compute_fisher(t_summary, c_summary)

    primary_gap = mean_gap if mean_gap is not None else uplift

    if t_summary.n_trials == 0 or c_summary.n_trials == 0:
        logger.warning("No trial data for one or both variants — defaulting to FAIL")
        recommendation = Recommendation.FAIL
    elif t_summary.n_passed == 0 and c_summary.n_passed == 0:
        logger.warning("Zero passes in both variants — defaulting to FAIL")
        recommendation = Recommendation.FAIL
    else:
        recommendation = Recommendation.PASS if primary_gap >= threshold else Recommendation.FAIL

    return AnalysisResult(
        submission_name=submission_name,
        provenance=provenance or Provenance(),
        summary=AnalysisSummary(
            related_pr=related_pr,
            llm=llm_label,
            treatment=t_summary,
            control=c_summary,
            uplift=uplift,
            mean_reward_gap=mean_gap,
            ttest_p_value=ttest_p,
            fisher_p_value=fisher_p,
            recommendation=recommendation,
        ),
        trials={
            "treatment": treatment_trials,
            "control": control_trials,
        },
    )


def _fmt(val: float | None, fmt: str = ".4f") -> str:
    return f"{val:{fmt}}" if val is not None else "N/A"


def _is_likert_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5


def _per_case_has_likert(result: AnalysisResult) -> bool:
    per_case = result.per_case or {}
    for case_data in per_case.values():
        if not isinstance(case_data, dict):
            continue
        for rec in case_data.values():
            if isinstance(rec, dict) and _is_likert_int(rec.get("value")):
                return True
            if _is_likert_int(rec):
                return True
    for tr in result.trials.get("treatment") or []:
        for rec in (tr.judges or {}).values():
            if isinstance(rec, dict) and _is_likert_int(rec.get("value")):
                return True
    return False


def _fmt_judge_mean(mean: Any, pass_rate: Any, *, likert: bool) -> str:
    if not isinstance(mean, (int, float)):
        return "—"
    if likert and pass_rate is None and 1.0 <= float(mean) <= 5.0 and float(mean) == int(mean):
        return f"{mean:.2f}/5"
    return _fmt(mean)


def _sig_marker(p: float | None) -> str:
    if p is None:
        return ""
    if p < 0.001:
        return " ***"
    if p < 0.01:
        return " **"
    if p < 0.05:
        return " *"
    return ""


def _is_single_sided(result: AnalysisResult) -> bool:
    """True when this report is one condition, not a treatment/control A/B."""
    if result.pairwise is not None:
        return False
    engine = (result.provenance.eval_engine or "").lower()
    if engine in {"a2a", "aeh", "aeh_openshell_openclaw"}:
        return True
    if result.mode == "single":
        return True
    return result.summary.control.n_trials == 0 and result.summary.treatment.n_trials > 0


def _fmt_judge_cell(rec: Any) -> str:
    if rec is None:
        return "—"
    if isinstance(rec, dict):
        if rec.get("error"):
            return "ERR"
        value = rec.get("value")
        if isinstance(value, bool):
            return "pass" if value else "fail"
        if _is_likert_int(value):
            return f"{value}/5"
        if isinstance(value, (int, float)):
            return str(value)
        return "—"
    if _is_likert_int(rec):
        return f"{rec}/5"
    return str(rec)


def render_markdown(result: AnalysisResult) -> str:
    """Render a human-readable Markdown report from analysis results."""
    s = result.summary
    t = s.treatment
    c = s.control
    prov = result.provenance
    single_sided = _is_single_sided(result)

    engine = (prov.eval_engine or "").lower()
    lines: list[str] = []
    if engine == "a2a":
        lines.append(f"# A2A Evaluation Report: {result.submission_name}\n")
        lines.append("*Single-sided (a2a) — not an A/B comparison.*\n")
    elif single_sided:
        engine_label = prov.eval_engine or "evaluation"
        lines.append(f"# Evaluation Report: {result.submission_name}\n")
        lines.append(f"*Single-sided ({engine_label}) — not an A/B comparison.*\n")
    else:
        lines.append(f"# A/B Evaluation Report: {result.submission_name}\n")

    # --- Summary table ---
    lines.append("## Summary\n")
    if s.related_pr:
        lines.append(f"* Related PR: {s.related_pr}")
    if s.llm:
        lines.append(f"* LLM: {s.llm}")
    if s.related_pr or s.llm:
        lines.append("")
    if single_sided:
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Trials | {t.n_trials} |")
        lines.append(f"| Passed | {t.n_passed} |")
        lines.append(f"| Failed | {t.n_failed} |")
        lines.append(f"| Errors | {t.n_errors} |")
        lines.append(f"| Pass Rate | {_fmt(t.pass_rate)} |")
        lines.append(f"| Mean Reward | {_fmt(t.mean_reward)} |")
        lines.append(f"| Median Reward | {_fmt(t.median_reward)} |")
        lines.append(f"| Std Reward | {_fmt(t.std_reward)} |")
        if _per_case_has_likert(result):
            lines.append("")
            lines.append(
                "*LLM judges use a 1–5 Likert scale. Harbor reward maps "
                "1→0.00 and 5→1.00; a raw mean of 1 is the floor, not a perfect 1.0.*"
            )
    else:
        lines.append("| Metric | Treatment | Control |")
        lines.append("|--------|-----------|---------|")
        lines.append(f"| Trials | {t.n_trials} | {c.n_trials} |")
        lines.append(f"| Passed | {t.n_passed} | {c.n_passed} |")
        lines.append(f"| Failed | {t.n_failed} | {c.n_failed} |")
        lines.append(f"| Errors | {t.n_errors} | {c.n_errors} |")
        lines.append(f"| Pass Rate | {_fmt(t.pass_rate)} | {_fmt(c.pass_rate)} |")
        lines.append(f"| Mean Reward | {_fmt(t.mean_reward)} | {_fmt(c.mean_reward)} |")
        lines.append(f"| Median Reward | {_fmt(t.median_reward)} | {_fmt(c.median_reward)} |")
        lines.append(f"| Std Reward | {_fmt(t.std_reward)} | {_fmt(c.std_reward)} |")
    lines.append("")

    # --- Comparison / results ---
    if single_sided:
        lines.append("## Results\n")
        lines.append(f"- **Mean reward:** {_fmt(t.mean_reward)}")
        lines.append(f"- **Pass rate:** {_fmt(t.pass_rate)}")
        if t.n_errors:
            lines.append(
                f"- **Errors:** {t.n_errors} trial(s) with no quality score "
                "(judge errors are not counted as 0.0)"
            )
    else:
        lines.append("## Comparison\n")
        if s.mean_reward_gap is not None:
            lines.append(f"- **Mean reward gap (Uplift):** {s.mean_reward_gap:+.4f}")
        else:
            lines.append(f"- **Uplift (pass rate gap):** {s.uplift:+.4f}")
        lines.append(f"- **Welch's t-test p-value:** {_fmt(s.ttest_p_value)}{_sig_marker(s.ttest_p_value)}")
        lines.append(f"- **Fisher's exact p-value:** {_fmt(s.fisher_p_value)}{_sig_marker(s.fisher_p_value)}")
    lines.append(f"- **Recommendation:** **{s.recommendation.value.upper()}**")
    lines.append("")

    if result.aeh_warnings:
        lines.append("## Aggregation notes\n")
        for warning in result.aeh_warnings:
            lines.append(f"- {warning}")
        lines.append("")

    if result.degradation is not None:
        d = result.degradation
        lines.append("## Degradation Check\n")
        lines.append(f"- **Status:** {'DEGRADED' if d.degraded else 'OK'}")
        lines.append(f"- **Message:** {d.message}")
        if d.threshold is not None:
            lines.append(f"- **Threshold:** {_fmt(d.threshold)}")
        if d.previous_pass_rate is not None:
            lines.append(f"- **Previous pass rate:** {_fmt(d.previous_pass_rate)}")
        if d.current_pass_rate is not None:
            lines.append(f"- **Current pass rate:** {_fmt(d.current_pass_rate)}")
        lines.append("")

    # --- Provenance ---
    lines.append("## Provenance\n")
    lines.append(f"- Generated at: {prov.generated_at.isoformat()}")
    lines.append(f"- Evaluation engine: {prov.eval_engine}")
    if prov.commit_sha:
        lines.append(f"- Commit SHA: `{prov.commit_sha}`")
    if prov.pipeline_run_id:
        lines.append(f"- Pipeline run: `{prov.pipeline_run_id}`")
    if prov.treatment_image_ref:
        lines.append(f"- Treatment image: `{prov.treatment_image_ref}`")
    if prov.control_image_ref:
        lines.append(f"- Control image: `{prov.control_image_ref}`")
    if prov.harbor_fork_revision:
        lines.append(f"- Harbor fork revision: `{prov.harbor_fork_revision}`")
    lines.append("")

    # --- Security scans ---
    if result.security_scans:
        lines.append("## Security Scans\n")
        for scan in result.security_scans:
            status = "PASSED" if scan.passed else "FAILED"
            lines.append(f"### {scan.scanner.upper()} Scanner [{status}]\n")
            lines.append(f"- **Mode:** {scan.scan_mode}")
            lines.append(f"- **Status:** {status}")
            counts = scan.severity_counts
            lines.append(
                f"- **Findings:** {len(scan.findings)} total "
                f"({counts['critical']} critical, {counts['high']} high, "
                f"{counts['medium']} medium, {counts['low']} low)"
            )
            if scan.findings:
                lines.append("\n<details>\n<summary>Finding Details</summary>\n")
                lines.append("| Severity | Rule | Message | File |")
                lines.append("|----------|------|---------|------|")
                for f in scan.findings[:20]:  # Limit to first 20
                    sev = f.severity.value.upper()
                    file_info = f.file_path or "-"
                    if f.line_number:
                        file_info += f":{f.line_number}"
                    msg = f.message[:60] + "..." if len(f.message) > 60 else f.message
                    lines.append(f"| {sev} | {f.rule_id} | {msg} | {file_info} |")
                if len(scan.findings) > 20:
                    lines.append(f"\n*... and {len(scan.findings) - 20} more findings*")
                lines.append("\n</details>\n")
            lines.append("")

    # --- Pairwise comparison (AEH) ---
    if result.pairwise is not None:
        pw = result.pairwise
        lines.append("## Pairwise Comparison\n")
        lines.append(f"- **Treatment run:** `{pw.run_a}`")
        lines.append(f"- **Control run:** `{pw.run_b}`")
        lines.append(f"- **Cases compared:** {pw.cases_compared}")
        lines.append(f"- **Treatment wins:** {pw.wins_a}")
        lines.append(f"- **Control wins:** {pw.wins_b}")
        lines.append(f"- **Ties:** {pw.ties}")
        lines.append(f"- **Errors:** {pw.errors}")
        lines.append(f"- **Treatment win rate:** {_fmt(pw.win_rate)}")
        if pw.per_case:
            lines.append("\n<details>\n<summary>Per-case winners</summary>\n")
            lines.append("| Case | Winner |")
            lines.append("|------|--------|")
            for entry in pw.per_case:
                if not isinstance(entry, dict):
                    continue
                case_id = entry.get("case_id") or entry.get("case") or entry.get("id") or "?"
                winner = entry.get("winner") or entry.get("verdict") or entry.get("result") or "?"
                lines.append(f"| {case_id} | {winner} |")
            lines.append("\n</details>\n")
        lines.append("")

    # --- AEH judges ---
    if result.judges:
        lines.append("## Judges\n")
        lines.append("| Judge | Mean | Pass rate | Errors |")
        lines.append("|-------|------|-----------|--------|")
        for name, data in result.judges.items():
            if isinstance(data, dict):
                mean = data.get("mean")
                rate = data.get("pass_rate")
                erred = data.get("errored_cases") or 0
                likert = _per_case_has_likert(result)
                mean_s = _fmt_judge_mean(mean, rate, likert=likert)
                rate_s = f"{rate:.0%}" if isinstance(rate, (int, float)) else "—"
                lines.append(f"| {name} | {mean_s} | {rate_s} | {erred} |")
            else:
                lines.append(f"| {name} | {data} | — | — |")
        lines.append("")

    judge_names: list[str] = []
    if result.per_case:
        for case_data in result.per_case.values():
            if isinstance(case_data, dict):
                for name in case_data:
                    if name != "reward" and name not in judge_names:
                        judge_names.append(name)
    elif result.trials.get("treatment"):
        for tr in result.trials["treatment"]:
            if tr.judges:
                for name in tr.judges:
                    if name not in judge_names:
                        judge_names.append(name)

    if judge_names:
        lines.append("## Per-case judges\n")
        header = "| Case | " + " | ".join(judge_names) + " |"
        sep = "|------|" + "|".join(["------"] * len(judge_names)) + "|"
        lines.append(header)
        lines.append(sep)
        if result.per_case:
            rows = result.per_case.items()
            for case_id, case_data in rows:
                cells = [
                    _fmt_judge_cell(case_data.get(name) if isinstance(case_data, dict) else None)
                    for name in judge_names
                ]
                lines.append(f"| {case_id} | " + " | ".join(cells) + " |")
        else:
            for tr in result.trials.get("treatment", []):
                cells = [_fmt_judge_cell((tr.judges or {}).get(name)) for name in judge_names]
                lines.append(f"| {tr.trial_name} | " + " | ".join(cells) + " |")
        lines.append("")

    # --- Per-trial details ---
    lines.append("## Trial Details\n")
    if single_sided:
        trials = result.trials.get("treatment", [])
        lines.append(f"<details>\n<summary>Cases ({len(trials)})</summary>\n")
        lines.append("| # | Trial | Reward | Passed |")
        lines.append("|---|-------|--------|--------|")
        for i, tr in enumerate(trials, 1):
            r_str = _fmt(tr.reward) if tr.reward is not None else "ERROR"
            p_str = "PASS" if tr.passed else "FAIL"
            lines.append(f"| {i} | {tr.trial_name} | {r_str} | {p_str} |")
        lines.append("\n</details>\n")
    else:
        for variant in VARIANTS:
            trials = result.trials.get(variant, [])
            lines.append(f"<details>\n<summary>{variant.capitalize()} ({len(trials)} trials)</summary>\n")
            lines.append("| # | Trial | Reward | Passed |")
            lines.append("|---|-------|--------|--------|")
            for i, tr in enumerate(trials, 1):
                r_str = _fmt(tr.reward) if tr.reward is not None else "ERROR"
                p_str = "PASS" if tr.passed else "FAIL"
                lines.append(f"| {i} | {tr.trial_name} | {r_str} | {p_str} |")
            lines.append("\n</details>\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Analyze A/B evaluation results and produce JSON + Markdown reports",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        help="Path to the results directory containing treatment/ and control/ subdirs",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory to write report.json and report.md",
    )
    parser.add_argument(
        "--submission-name",
        help="Name of the submission being analyzed",
    )
    parser.add_argument(
        "--merge-degradation-from",
        type=Path,
        default=None,
        help="Path to monitor.py JSON output to merge into an existing report.json",
    )
    parser.add_argument(
        "--report-json",
        type=Path,
        default=None,
        help="Path to report.json when merging degradation results",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.0,
        help="Minimum uplift for a 'pass' recommendation (default: 0.0)",
    )
    parser.add_argument("--commit-sha", default=None)
    parser.add_argument("--pipeline-run-id", default=None)
    parser.add_argument("--treatment-image-ref", default=None)
    parser.add_argument("--control-image-ref", default=None)
    parser.add_argument("--harbor-fork-revision", default=None)
    parser.add_argument(
        "--eval-engine",
        type=str,
        choices=["harbor", "ase", "both", "a2a", "aeh", "aeh_openshell_openclaw"],
        default="harbor",
        help="Evaluation engine used (for provenance tagging and analysis path)",
    )
    parser.add_argument(
        "--security-scan-mode",
        type=str,
        choices=["disabled", "warn", "block"],
        default="disabled",
        help="Cisco security scan mode (disabled, warn, block)",
    )
    parser.add_argument(
        "--pr-url",
        type=str,
        default=None,
        help="URL of the PR that triggered this evaluation",
    )
    parser.add_argument(
        "--llm-label",
        type=str,
        default=None,
        help="LLM model label for the report (e.g. 'Claude Sonnet 4.6 (vertex_ai)')",
    )
    args = parser.parse_args(argv)

    if args.merge_degradation_from is not None:
        report_path = args.report_json
        if report_path is None and args.output_dir is not None:
            report_path = args.output_dir / "report.json"
        if report_path is None or not report_path.is_file():
            logger.error("report.json not found for degradation merge (use --report-json or --output-dir)")
            return 1
        if not args.merge_degradation_from.is_file():
            logger.error("Monitor output file does not exist: %s", args.merge_degradation_from)
            return 1
        merge_degradation_into_report(report_path, args.merge_degradation_from)
        return 0

    if args.results_dir is None or args.output_dir is None or args.submission_name is None:
        logger.error("--results-dir, --output-dir, and --submission-name are required for analysis")
        return 1

    if not args.results_dir.is_dir():
        logger.error("Results directory does not exist: %s", args.results_dir)
        return 1

    provenance = Provenance(
        commit_sha=args.commit_sha,
        pipeline_run_id=args.pipeline_run_id,
        treatment_image_ref=args.treatment_image_ref,
        control_image_ref=args.control_image_ref,
        harbor_fork_revision=args.harbor_fork_revision,
        eval_engine=args.eval_engine,
    )

    result = build_analysis(
        results_dir=args.results_dir,
        submission_name=args.submission_name,
        threshold=args.threshold,
        provenance=provenance,
        related_pr=args.pr_url,
        llm_label=args.llm_label,
        eval_engine=args.eval_engine,
    )

    # Include security scan results if available
    if args.security_scan_mode != "disabled":
        security_result = parse_security_scan(args.output_dir, args.security_scan_mode)
        if security_result:
            result.security_scans.append(security_result)
            logger.info(
                "Security scan: %d findings (%d critical, %d high), passed=%s",
                len(security_result.findings),
                security_result.severity_counts["critical"],
                security_result.severity_counts["high"],
                security_result.passed,
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)

    json_path = args.output_dir / "report.json"
    json_path.write_text(result.model_dump_json(indent=2))
    logger.info("Wrote JSON report to %s", json_path)

    md_path = args.output_dir / "report.md"
    md_path.write_text(render_markdown(result))
    logger.info("Wrote Markdown report to %s", md_path)

    s = result.summary
    print(f"Treatment mean reward: {_fmt(s.treatment.mean_reward)}")
    print(f"Control mean reward:   {_fmt(s.control.mean_reward)}")
    print(f"Mean reward gap:       {_fmt(s.mean_reward_gap, '+.4f')}")
    print(f"Uplift (pass rate):    {s.uplift:+.4f}")
    print(f"Recommendation:        {s.recommendation.value.upper()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
