"""Command interface for aggregate aeh."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from abevalflow.aeh_scoring import DEFAULT_AEH_THRESHOLD
from abevalflow.results.aeh import _log_judge_tables, aggregate_aeh_results, logger


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
        choices=["aeh"],
        help="Stamp the AEH engine in report.json",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=(
            "Path to eval.yaml (single mode). Used to read each judge's declared "
            "score_range so bare numeric values like 1 are normalized unambiguously."
        ),
    )
    parser.add_argument(
        "--treatment-config",
        type=Path,
        default=None,
        help="Path to eval-treatment.yaml (pairwise mode). Same purpose as --config.",
    )
    parser.add_argument(
        "--control-config",
        type=Path,
        default=None,
        help="Path to eval-control.yaml (pairwise mode). Same purpose as --config.",
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
            config_path=args.config,
            treatment_config_path=args.treatment_config,
            control_config_path=args.control_config,
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
