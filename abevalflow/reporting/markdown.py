"""Reporting markdown services."""

from __future__ import annotations

from typing import Any

from abevalflow.report import AnalysisResult

VARIANTS = ("treatment", "control")


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
    if engine == "aeh":
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

    lines: list[str] = []
    if single_sided:
        engine_label = prov.eval_engine or "evaluation"
        lines.append(f"# Evaluation Report: {result.submission_name}\n")
        lines.append(f"*Single-sided ({engine_label}) — not an A/B comparison.*\n")
    else:
        lines.append(f"# A/B Evaluation Report: {result.submission_name}\n")

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

    if single_sided:
        lines.append("## Results\n")
        lines.append(f"- **Mean reward:** {_fmt(t.mean_reward)}")
        lines.append(f"- **Pass rate:** {_fmt(t.pass_rate)}")
        if t.n_errors:
            lines.append(
                f"- **Errors:** {t.n_errors} trial(s) with no quality score (judge errors are not counted as 0.0)"
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
    lines.append("")

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
                for f in scan.findings[:20]:
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
