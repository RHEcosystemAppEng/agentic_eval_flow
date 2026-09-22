#!/usr/bin/env python3
"""Map AEH output to Agentic Eval Flow report format.

Reads agent-eval-harness output files (summary.yaml, run_result.json) and
produces a unified report.json compatible with Agentic Eval Flow's scorecard logic.

Supports both single-run and pairwise modes:
  - Single: One run directory
  - Pairwise: Treatment and control directories with pairwise comparison results

Usage:
    # Single mode (default)
    python scripts/aggregate_aeh.py <run_dir> [--output <path>]

    # Pairwise mode
    python scripts/aggregate_aeh.py <treatment_dir> --mode pairwise --control-dir <control_dir>

Where <run_dir> is the AEH output directory containing:
    - summary.yaml: Per-judge means, per-case results, run metadata, pairwise results
    - run_result.json: Execution metadata (duration, cost, tokens)
"""

import argparse
import json
import logging
import statistics
import sys
from pathlib import Path
from typing import Any

import yaml

from abevalflow.aeh_scoring import (
    DEFAULT_AEH_THRESHOLD,
    pairwise_outcome,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# Top-level run_result.json keys surfaced in report.json execution metadata.
# Never copy per_case / response_text (those can be huge).
_OPENSHELL_EXECUTION_KEYS = ("execution_mode", "agent", "n_cases", "n_failed", "wall_clock_s")


def _load_execution_metadata(run_dir: Path) -> dict[str, Any]:
    """Load execution metadata from run_result.json.

    Harbor and OpenShell share this block. OpenShell fields (execution_mode,
    agent, n_cases, n_failed, wall_clock_s) are included when present.
    """
    run_result_path = run_dir / "run_result.json"
    if not run_result_path.exists():
        return {}

    try:
        run_result = json.loads(run_result_path.read_text())
        duration = run_result.get("duration_s")
        if duration is None:
            duration = run_result.get("wall_clock_s")
        meta: dict[str, Any] = {
            "duration_s": duration,
            "cost_usd": run_result.get("cost_usd"),
            "tokens": run_result.get("token_usage"),
            "harbor_job_dir": run_result.get("harbor_job_dir"),
            "num_turns": run_result.get("num_turns"),
            "n_infra_errors": run_result.get("n_infra_errors"),
            "n_trial_errors": run_result.get("n_trial_errors"),
        }
        for key in _OPENSHELL_EXECUTION_KEYS:
            if key in run_result:
                meta[key] = run_result[key]
        if "n_cases" not in meta and run_result.get("num_cases") is not None:
            meta["n_cases"] = run_result["num_cases"]
        return meta
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load run_result.json: %s", e)
        return {}


def _load_summary(run_dir: Path) -> dict[str, Any]:
    summary_path = run_dir / "summary.yaml"
    if not summary_path.exists():
        return {}
    try:
        data = yaml.safe_load(summary_path.read_text())
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _compact_judge(result: Any) -> dict[str, Any] | Any:
    """Keep value/error/judge_type; drop rationale and other bulky fields."""
    if not isinstance(result, dict):
        return result
    compact: dict[str, Any] = {}
    if "value" in result:
        compact["value"] = result["value"]
    if result.get("error"):
        compact["error"] = str(result["error"])
    if result.get("judge_type"):
        compact["judge_type"] = result["judge_type"]
    return compact


def _compact_per_case(per_case: Any) -> dict[str, Any]:
    if not isinstance(per_case, dict):
        return {}
    out: dict[str, Any] = {}
    for case_id, case_data in per_case.items():
        if not isinstance(case_data, dict):
            out[str(case_id)] = case_data
            continue
        out[str(case_id)] = {
            key: _compact_judge(val) if key != "reward" else val for key, val in case_data.items()
        }
    return out


def _normalize_numeric_value(value: int | float) -> float:
    """Map a numeric judge onto [0, 1] using the same Likert vs unit scale as aeh_scoring.

    Integer 1–5 is a Likert score (Harbor ``score_range: [1, 5]``): 1 is the
    floor (reward 0.0), 5 is the ceiling (reward 1.0). A unit-scale perfect
    score must be the float ``1.0``, not the integer ``1`` — YAML ``value: 1``
    from ``feedback_type: int`` is the worst rubric bucket, not 100%.
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int) and 1 <= value <= 5:
        return (value - 1) / 4.0
    v = float(value)
    if 0.0 <= v <= 1.0:
        return v
    if 1.0 < v <= 5.0:
        return (v - 1.0) / 4.0
    return v


def _is_likert_int(value: Any) -> bool:
    """True for a 1–5 integer rubric score (not bool, not unit-scale 1.0)."""
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5


def _fmt_likert_cell(value: Any) -> str:
    if isinstance(value, bool):
        return "pass" if value else "fail"
    if _is_likert_int(value):
        return f"{value}/5"
    if isinstance(value, (int, float)):
        return str(value)
    return "—"


def _fmt_judge_mean_for_log(mean: Any, pass_rate: Any) -> str:
    """Label Likert means as /5 so a floor of 1 is not read as unit-scale 1.000."""
    if not isinstance(mean, (int, float)):
        return "—"
    if pass_rate is None and (_is_likert_int(mean) or (isinstance(mean, float) and 1.0 <= mean <= 5.0)):
        # Integer 1–5 or a 1.0–5.0 mean with no pass_rate is the Likert table.
        if mean <= 5.0 and mean >= 1.0 and (pass_rate is None):
            # Unit-scale means live in [0, 1]; a mean of exactly 1.0 with no
            # pass_rate is still ambiguous, but AEH LLM rubrics in this pipeline
            # are Likert. Show /5 when the mean is an integer-valued 1–5.
            if float(mean) == int(mean) and 1 <= int(mean) <= 5:
                return f"{mean:.2f}/5"
    return f"{mean:.3f}"


def _judge_errored(result: Any) -> bool:
    return isinstance(result, dict) and bool(result.get("error")) and result.get("value") is None


def _is_llm_or_numeric_judge(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    jt = result.get("judge_type")
    if jt == "llm":
        return True
    if jt == "check":
        return False
    value = result.get("value")
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _case_reward(case_data: Any) -> float | None:
    """Derive a single reward from an AEH per_case entry.

    Successful numeric judges are normalized to [0, 1] and averaged. Failed
    boolean judges gate the case to 0.0. Errored judges are skipped — they are
    not treated as 0.0. If the only remaining signal is booleans but an LLM
    judge errored, return None so operational checks cannot masquerade as a
    quality score.
    """
    if not isinstance(case_data, dict):
        return None

    if isinstance(case_data.get("reward"), (int, float)) and not isinstance(case_data.get("reward"), bool):
        return float(case_data["reward"])

    numeric: list[float] = []
    bools: list[bool] = []
    scoring_judge_errored = False
    for key, result in case_data.items():
        if key == "reward":
            continue
        if _judge_errored(result):
            if _is_llm_or_numeric_judge(result) or (
                isinstance(result, dict) and result.get("judge_type") == "llm"
            ):
                scoring_judge_errored = True
            elif isinstance(result, dict) and result.get("judge_type") not in ("check", "builtin"):
                scoring_judge_errored = True
            continue
        if isinstance(result, dict) and "value" in result:
            value = result.get("value")
        else:
            value = result
        if isinstance(value, bool):
            bools.append(value)
        elif isinstance(value, (int, float)):
            numeric.append(_normalize_numeric_value(value))

    if numeric:
        if bools and not all(bools):
            return 0.0
        return sum(numeric) / len(numeric)
    if bools:
        if scoring_judge_errored:
            return None
        return 1.0 if all(bools) else 0.0
    return None


def _mean_reward_from_per_case(per_case: Any) -> float | None:
    if not isinstance(per_case, dict) or not per_case:
        return None
    rewards = [_case_reward(case_data) for case_data in per_case.values()]
    scored = [r for r in rewards if r is not None]
    if not scored:
        return None
    return sum(scored) / len(scored)


def _scoring_unavailable_from_errors(per_case: Any) -> bool:
    """True when LLM/numeric judges errored and no case has a quality reward."""
    if not isinstance(per_case, dict) or not per_case:
        return False
    has_scoring_error = False
    for case_data in per_case.values():
        if not isinstance(case_data, dict):
            continue
        for rec in case_data.values():
            if _judge_errored(rec) and (
                _is_llm_or_numeric_judge(rec)
                or (isinstance(rec, dict) and rec.get("judge_type") == "llm")
            ):
                has_scoring_error = True
                break
        if has_scoring_error:
            break
    if not has_scoring_error:
        return False
    return all(_case_reward(case_data) is None for case_data in per_case.values())


def _parse_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_mean_reward(run_dir: Path) -> float | None:
    """Extract mean_reward from run_result.json, summary.yaml, or per-case judges.

    Missing or all-errored scoring judges yield None — never a silent 0.0 quality
    score. Declared mean_reward is used when present unless judge errors made
    quality scoring unavailable (OpenShell often omits mean_reward entirely).
    """
    summary = _load_summary(run_dir)
    per_case = summary.get("per_case")
    from_cases = _mean_reward_from_per_case(per_case)
    unavailable = _scoring_unavailable_from_errors(per_case)

    declared = _parse_optional_float(summary.get("mean_reward"))
    run_declared: float | None = None
    run_result_path = run_dir / "run_result.json"
    if run_result_path.exists():
        try:
            run_result = json.loads(run_result_path.read_text())
            if isinstance(run_result, dict):
                run_declared = _parse_optional_float(run_result.get("mean_reward"))
        except (json.JSONDecodeError, OSError):
            pass

    if unavailable:
        return None
    if from_cases is not None and declared is None and run_declared is None:
        return from_cases
    if declared is not None:
        return declared
    if run_declared is not None:
        return run_declared
    return from_cases


def _trials_from_per_case(per_case: Any) -> list[dict[str, Any]]:
    """Map AEH per_case dict → TrialResult-shaped list for report.json."""
    if not isinstance(per_case, dict):
        return []
    trials: list[dict[str, Any]] = []
    for case_id, case_data in per_case.items():
        judges = None
        if isinstance(case_data, dict):
            judges = {
                key: _compact_judge(val)
                for key, val in case_data.items()
                if key != "reward"
            } or None
        trials.append(
            {
                "trial_name": str(case_id),
                "reward": _case_reward(case_data),
                "judges": judges,
            }
        )
    return trials


def _empty_control_summary() -> dict[str, Any]:
    """Placeholder control block for single-sided runs — not a fake A/B control."""
    return {
        "n_trials": 0,
        "n_passed": 0,
        "n_failed": 0,
        "n_errors": 0,
        "pass_rate": 0.0,
        "mean_reward": None,
        "median_reward": None,
        "std_reward": None,
    }


def _log_judge_tables(report: dict[str, Any]) -> None:
    """Print a concise per-judge and per-case table for analyze/evaluate logs."""
    judges = report.get("judges") or {}
    if isinstance(judges, dict) and judges:
        logger.info("Judge aggregates:")
        logger.info("  %-32s %10s %10s %8s", "name", "mean", "pass_rate", "errors")
        for name, data in judges.items():
            if not isinstance(data, dict):
                logger.info("  %-32s %s", name, data)
                continue
            mean = data.get("mean")
            rate = data.get("pass_rate")
            erred = data.get("errored_cases") or 0
            mean_s = _fmt_judge_mean_for_log(mean, rate) if isinstance(mean, (int, float)) else "—"
            rate_s = f"{rate:.0%}" if isinstance(rate, (int, float)) else "—"
            logger.info("  %-32s %10s %10s %8s", name, mean_s, rate_s, erred)

    per_case = report.get("per_case") or {}
    if not isinstance(per_case, dict) or not per_case:
        return
    judge_names: list[str] = []
    for case_data in per_case.values():
        if isinstance(case_data, dict):
            for name in case_data:
                if name != "reward" and name not in judge_names:
                    judge_names.append(name)
    if not judge_names:
        return
    header = "  %-22s" + " %s" * len(judge_names)
    logger.info("Per-case judges:")
    logger.info(header, "case", *[n[:16] for n in judge_names])
    for case_id, case_data in per_case.items():
        cells: list[str] = []
        for name in judge_names:
            cell = "—"
            if isinstance(case_data, dict):
                rec = case_data.get(name)
                if isinstance(rec, dict):
                    if rec.get("error"):
                        cell = "ERR"
                    elif "value" in rec:
                        cell = _fmt_likert_cell(rec.get("value"))
                elif rec is not None:
                    cell = str(rec)
            cells.append(cell)
        logger.info(header, str(case_id)[:22], *cells)


def aggregate_single_run(
    run_dir: Path,
    *,
    submission_name: str | None = None,
    threshold: float = DEFAULT_AEH_THRESHOLD,
    eval_engine: str = "aeh",
) -> dict[str, Any]:
    """Read one AEH harness run directory and produce report dict.

    AEH output layout (from harbor.run):
        <run_dir>/summary.yaml      # run_id, judges, per_case, run_metrics
        <run_dir>/run_result.json   # duration, cost, tokens, mean_reward
        <run_dir>/cases/...         # per-case artifacts

    Args:
        run_dir: Path to the AEH run output directory
        submission_name: Override for report submission_name (Tekton param)
        threshold: Pass/fail threshold for mean_reward (matches GatePolicy default)

    Returns:
        Dict in Agentic Eval Flow report format with full judge metadata
    """
    summary_path = run_dir / "summary.yaml"

    if not summary_path.exists():
        raise FileNotFoundError(f"summary.yaml not found in {run_dir}")

    summary = yaml.safe_load(summary_path.read_text()) or {}
    mean_reward = _extract_mean_reward(run_dir)

    judges_full = summary.get("judges", {}) or {}
    per_case_full = summary.get("per_case", {}) or {}
    per_case_compact = _compact_per_case(per_case_full)
    run_metrics = summary.get("run_metrics")
    trials = _trials_from_per_case(per_case_full)

    total_cases = len(per_case_full) if isinstance(per_case_full, dict) else 0
    n_passed = 0
    n_failed = 0
    n_errors = 0
    case_rewards: list[float] = []
    warnings: list[str] = []
    for trial in trials:
        reward = trial.get("reward")
        if reward is None:
            n_errors += 1
        else:
            case_rewards.append(float(reward))
            if reward > 0.0:
                n_passed += 1
            else:
                n_failed += 1

    judge_error_count = 0
    if isinstance(per_case_full, dict):
        for case_data in per_case_full.values():
            if not isinstance(case_data, dict):
                continue
            for rec in case_data.values():
                if _judge_errored(rec):
                    judge_error_count += 1
    if judge_error_count:
        warnings.append(
            f"{judge_error_count} judge error(s) excluded from mean_reward "
            "(not scored as 0.0 quality)"
        )
    if mean_reward is None and judge_error_count:
        warnings.append("mean_reward unavailable because scoring judges errored")

    pass_rate = n_passed / total_cases if total_cases > 0 else 0.0
    recommendation = "pass" if (mean_reward is not None and mean_reward >= threshold) else "fail"
    resolved_name = submission_name or (run_dir.parent.name if run_dir.parent != run_dir else run_dir.name)
    median_reward = statistics.median(case_rewards) if case_rewards else None
    std_reward = statistics.stdev(case_rewards) if len(case_rewards) > 1 else None

    # Single-sided: empty control with None rewards, not a fake 0.0 A/B arm.
    return {
        "submission_name": resolved_name,
        "provenance": {
            "eval_engine": eval_engine,
            "pipeline_run_id": summary.get("run_id", run_dir.name),
        },
        "summary": {
            "treatment": {
                "n_trials": total_cases,
                "n_passed": n_passed,
                "n_failed": n_failed,
                "n_errors": n_errors,
                "pass_rate": pass_rate,
                "mean_reward": mean_reward,
                "median_reward": median_reward,
                "std_reward": std_reward,
            },
            "control": _empty_control_summary(),
            "uplift": pass_rate,
            "mean_reward_gap": None,
            "recommendation": recommendation,
        },
        "trials": {
            "treatment": trials,
            "control": [],
        },
        "eval_engine": eval_engine,
        "mode": "single",
        "run_id": summary.get("run_id", run_dir.name),
        "mean_reward": mean_reward,
        "pass_rate": pass_rate,
        "total_cases": total_cases,
        "passed_cases": n_passed,
        "judges": judges_full,
        "per_case": per_case_compact,
        "run_metrics": run_metrics,
        "execution": _load_execution_metadata(run_dir),
        "aeh_warnings": warnings,
        "recommendation": recommendation,
    }


def aggregate_pairwise_run(
    treatment_dir: Path,
    control_dir: Path,
    *,
    submission_name: str | None = None,
    threshold: float = DEFAULT_AEH_THRESHOLD,
    eval_engine: str = "aeh",
) -> dict[str, Any]:
    """Read pairwise AEH run directories and produce report dict.

    Pairwise mode expects:
        - Two run directories (treatment and control)
        - Treatment summary.yaml contains `pairwise` section from score.py pairwise

    Args:
        treatment_dir: Path to the treatment (A) run directory
        control_dir: Path to the control (B) run directory
        submission_name: Override for report submission_name (Tekton param)
        threshold: Pass/fail win-rate threshold (matches GatePolicy / engine)

    Returns:
        Dict in Agentic Eval Flow report format with pairwise results
    """
    treatment_summary_path = treatment_dir / "summary.yaml"
    control_summary_path = control_dir / "summary.yaml"

    if not treatment_summary_path.exists():
        raise FileNotFoundError(f"summary.yaml not found in {treatment_dir}")

    treatment_summary = yaml.safe_load(treatment_summary_path.read_text())
    treatment_mean_reward = _extract_mean_reward(treatment_dir)

    control_summary = {}
    control_mean_reward = 0.0
    if control_summary_path.exists():
        control_summary = yaml.safe_load(control_summary_path.read_text())
        control_mean_reward = _extract_mean_reward(control_dir)

    # Extract pairwise results from treatment summary
    pairwise = treatment_summary.get("pairwise", {})
    outcome = pairwise_outcome(
        pairwise.get("wins_a", 0),
        pairwise.get("wins_b", 0),
        pairwise.get("ties", 0),
        pairwise.get("errors", 0),
        threshold=threshold,
    )
    wins_a = outcome["wins_a"]
    wins_b = outcome["wins_b"]
    ties = outcome["ties"]
    errors = outcome["errors"]
    total = outcome["total"]
    win_rate = outcome["win_rate"]
    recommendation = outcome["recommendation"]
    cases_compared = pairwise.get("cases_compared", total)

    treatment_judges = treatment_summary.get("judges", {})
    treatment_per_case = treatment_summary.get("per_case", {})
    control_judges = control_summary.get("judges", {})
    control_per_case = control_summary.get("per_case", {})
    treatment_per_case_compact = _compact_per_case(treatment_per_case)
    control_per_case_compact = _compact_per_case(control_per_case)

    resolved_name = submission_name or (
        treatment_dir.parent.name if treatment_dir.parent != treatment_dir else treatment_dir.name
    )
    t_mean = treatment_mean_reward if treatment_mean_reward is not None else 0.0
    c_mean = control_mean_reward if control_mean_reward is not None else 0.0
    treatment_cases = len(treatment_per_case) if isinstance(treatment_per_case, dict) else 0
    control_cases = len(control_per_case) if isinstance(control_per_case, dict) else 0

    return {
        "submission_name": resolved_name,
        "provenance": {
            "eval_engine": eval_engine,
            "pipeline_run_id": treatment_summary.get("run_id", treatment_dir.name),
        },
        "summary": {
            "treatment": {
                "n_trials": treatment_cases,
                "n_passed": wins_a,
                "n_failed": max(treatment_cases - wins_a, 0),
                "pass_rate": win_rate,
                "mean_reward": treatment_mean_reward,
            },
            "control": {
                "n_trials": control_cases,
                "n_passed": wins_b,
                "n_failed": max(control_cases - wins_b, 0),
                "pass_rate": (wins_b / total) if total > 0 else 0.0,
                "mean_reward": control_mean_reward,
            },
            "uplift": win_rate - ((wins_b / total) if total > 0 else 0.0),
            "mean_reward_gap": t_mean - c_mean,
            "recommendation": recommendation,
        },
        "trials": {
            "treatment": _trials_from_per_case(treatment_per_case),
            "control": _trials_from_per_case(control_per_case),
        },
        "eval_engine": eval_engine,
        "mode": "pairwise",
        "treatment": {
            "run_id": treatment_summary.get("run_id", treatment_dir.name),
            "mean_reward": treatment_mean_reward,
            "judges": treatment_judges,
            "per_case": treatment_per_case_compact,
            "run_metrics": treatment_summary.get("run_metrics"),
            "execution": _load_execution_metadata(treatment_dir),
        },
        "control": {
            "run_id": control_summary.get("run_id", control_dir.name),
            "mean_reward": control_mean_reward,
            "judges": control_judges,
            "per_case": control_per_case_compact,
            "run_metrics": control_summary.get("run_metrics"),
            "execution": _load_execution_metadata(control_dir),
        },
        "pairwise": {
            "run_a": pairwise.get("run_a", treatment_dir.name),
            "run_b": pairwise.get("run_b", control_dir.name),
            "cases_compared": cases_compared,
            "wins_a": wins_a,
            "wins_b": wins_b,
            "ties": ties,
            "errors": errors,
            "win_rate": win_rate,
            "per_case": pairwise.get("per_case", []),
            "stability": pairwise.get("stability"),
        },
        "mean_reward": treatment_mean_reward,
        "judges": treatment_judges,
        "per_case": treatment_per_case_compact,
        "aeh_warnings": (["pairwise section missing from treatment summary.yaml"] if not pairwise else []),
        "recommendation": recommendation,
    }


def aggregate_aeh_results(
    run_dir: Path,
    mode: str = "single",
    control_dir: Path | None = None,
    *,
    submission_name: str | None = None,
    threshold: float = DEFAULT_AEH_THRESHOLD,
    eval_engine: str = "aeh",
) -> dict[str, Any]:
    """Aggregate AEH results into Agentic Eval Flow report format.

    Args:
        run_dir: Path to the AEH run output directory (treatment in pairwise mode)
        mode: Either "single" or "pairwise"
        control_dir: Path to control directory (required for pairwise mode)
        submission_name: Override for report submission_name
        threshold: Pass/fail threshold aligned with AEHEngine / GatePolicy default

    Returns:
        Dict in Agentic Eval Flow report format
    """
    if mode == "pairwise":
        if control_dir is None:
            raise ValueError("control_dir is required for pairwise mode")
        return aggregate_pairwise_run(
            run_dir,
            control_dir,
            submission_name=submission_name,
            threshold=threshold,
            eval_engine=eval_engine,
        )
    return aggregate_single_run(
        run_dir,
        submission_name=submission_name,
        threshold=threshold,
        eval_engine=eval_engine,
    )


def find_latest_run_dir(reports_dir: Path, submission_name: str) -> Path | None:
    """Find the most recent run directory for a submission.

    Layout: reports/<submission_name>/<run_id>/summary.yaml

    Args:
        reports_dir: Root reports directory
        submission_name: Name of the submission

    Returns:
        Path to the latest run dir, or None if not found
    """
    submission_dir = reports_dir / submission_name
    if not submission_dir.exists():
        return None

    run_dirs = [d for d in submission_dir.iterdir() if d.is_dir() and (d / "summary.yaml").exists()]
    if not run_dirs:
        return None

    return sorted(run_dirs, key=lambda d: d.stat().st_mtime, reverse=True)[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate AEH results into Agentic Eval Flow report format")
    parser.add_argument(
        "run_dir",
        type=Path,
        help="Path to AEH run directory containing summary.yaml (treatment dir in pairwise mode)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="Output path for report.json (default: <run_dir>/report.json)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["single", "pairwise"],
        default="single",
        help="Aggregation mode (single or pairwise)",
    )
    parser.add_argument(
        "--control-dir",
        type=Path,
        default=None,
        help="Path to control run directory (required for pairwise mode)",
    )
    parser.add_argument(
        "--submission-name",
        type=str,
        default=None,
        help="Override submission_name in report.json (Tekton submission-name)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_AEH_THRESHOLD,
        help=f"Pass/fail threshold (default: {DEFAULT_AEH_THRESHOLD})",
    )
    parser.add_argument(
        "--eval-engine",
        type=str,
        default="aeh",
        help="Stamp eval_engine in report.json (aeh or aeh_openshell_openclaw)",
    )
    args = parser.parse_args(argv)

    run_dir: Path = args.run_dir
    if not run_dir.is_dir():
        logger.error("Not a directory: %s", run_dir)
        return 1

    if args.mode == "pairwise" and args.control_dir is None:
        logger.error("--control-dir is required for pairwise mode")
        return 1

    if args.control_dir and not args.control_dir.is_dir():
        logger.error("Control directory not found: %s", args.control_dir)
        return 1

    try:
        report = aggregate_aeh_results(
            run_dir,
            mode=args.mode,
            control_dir=args.control_dir,
            submission_name=args.submission_name,
            threshold=args.threshold,
            eval_engine=args.eval_engine,
        )
    except FileNotFoundError as e:
        logger.error(str(e))
        return 1

    output_path = args.output or (run_dir / "report.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2))
    logger.info("Wrote report to: %s", output_path)

    if args.mode == "pairwise":
        pairwise = report.get("pairwise", {})
        logger.info(
            "Pairwise: treatment wins %d/%d (%.0f%%), ties=%d, errors=%d",
            pairwise.get("wins_a", 0),
            pairwise.get("cases_compared", 0),
            pairwise.get("win_rate", 0) * 100,
            pairwise.get("ties", 0),
            pairwise.get("errors", 0),
        )
    else:
        mean_reward = report["mean_reward"]
        mean_reward_str = f"{mean_reward:.3f}" if mean_reward is not None else "unavailable"
        logger.info(
            "Single-sided AEH: mean_reward=%s, pass_rate=%.2f (%d/%d cases), n_errors=%s",
            mean_reward_str,
            report.get("pass_rate", 0),
            report.get("passed_cases", 0),
            report.get("total_cases", 0),
            (report.get("summary") or {}).get("treatment", {}).get("n_errors", 0),
        )
        for warning in report.get("aeh_warnings") or []:
            logger.warning("%s", warning)
        _log_judge_tables(report)

    return 0


if __name__ == "__main__":
    sys.exit(main())
