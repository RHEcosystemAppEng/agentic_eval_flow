#!/usr/bin/env python3
"""Generate unified phase3-summary.json from mcpchecker-report.json.

Reads the detailed MCPCheckerResult and writes a unified summary matching
the Phase 1/2 format for cross-phase consistency.

Usage:
    python -m scripts.mcp.runners.phase3_summary --report-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REPORT_FILENAME = "mcpchecker-report.json"
SUMMARY_FILENAME = "phase3-summary.json"


def map_mcpchecker_to_summary(result: dict) -> dict:
    """Map MCPCheckerResult to unified phase3-summary.json format.

    Args:
        result: Parsed mcpchecker-report.json dict

    Returns:
        Unified summary dict matching Phase 1/2 format
    """
    checks = []
    for task in result.get("tasks", []):
        # Map status to three-state model
        status_map = {
            "passed": "pass",
            "failed": "fail",
            "error": "not_evaluated",
            "skipped": "not_evaluated",
        }
        task_status = task.get("status", "error")
        status = status_map.get(task_status, "not_evaluated")

        # Score: use judge pass rate if available, else binary
        judge_pass_rate = task.get("llm_judge_pass_rate")
        if task_status == "passed":
            score = judge_pass_rate if judge_pass_rate is not None else 1.0
        elif task_status == "failed":
            score = judge_pass_rate if judge_pass_rate is not None else 0.0
        else:  # error or skipped
            score = 0.0

        # Findings: count of failed judge checks
        findings = sum(1 for j in task.get("llm_judge_results", []) if not j.get("passed", True))

        # Message: error or summary
        task_name = task.get("task_name", "unknown")
        error_msg = task.get("error_message")
        if error_msg:
            message = error_msg
        elif task_status == "passed":
            message = f"All checks passed for {task_name}"
        else:
            message = f"{task_name} evaluation {task_status}"

        checks.append(
            {
                "check": task_name,
                "status": status,
                "passed": task_status == "passed",
                "score": score,
                "findings": findings,
                "source": "mcpchecker",
                "message": message,
            }
        )

    return {
        "phase": "phase3",
        "passed": result.get("recommendation") == "pass",
        "counts": {
            "pass": result.get("passed_tasks", 0),
            "fail": result.get("failed_tasks", 0),
            "not_evaluated": result.get("error_tasks", 0) + result.get("skipped_tasks", 0),
        },
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Phase 3 unified summary")
    parser.add_argument(
        "--report-dir",
        type=Path,
        required=True,
        help="Directory containing mcpchecker-report.json",
    )
    args = parser.parse_args()

    if not args.report_dir.is_dir():
        logger.error("Not a directory: %s", args.report_dir)
        return 1

    report_path = args.report_dir / REPORT_FILENAME
    if not report_path.exists():
        logger.error("Report not found: %s", report_path)
        return 1

    # Read mcpchecker-report.json
    try:
        with open(report_path) as f:
            result = json.load(f)
    except Exception as e:
        logger.error("Failed to read report: %s", e)
        return 1

    # Generate unified summary
    summary = map_mcpchecker_to_summary(result)

    # Write phase3-summary.json
    summary_path = args.report_dir / SUMMARY_FILENAME
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    logger.info("Wrote unified summary to %s", summary_path)
    logger.info(
        "Phase 3 overall: %s (pass=%d fail=%d not_evaluated=%d)",
        "PASS" if summary["passed"] else "FAIL",
        summary["counts"]["pass"],
        summary["counts"]["fail"],
        summary["counts"]["not_evaluated"],
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
