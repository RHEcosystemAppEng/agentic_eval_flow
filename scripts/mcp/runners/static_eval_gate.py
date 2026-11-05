#!/usr/bin/env python3
"""Run MCP static evaluation gates over normalized scan reports.
Reads normalized scan JSON files from results-dir and writes:
- <gate>-result.json per gate into results-dir
- static-eval-summary.json into reports-dir
Usage:
    python -m scripts.mcp.runners.static_eval_gate \
        --results-dir results \
        --reports-dir reports \
        --mode block
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from abevalflow.mcp.gates import run_static_eval
from abevalflow.schemas import GateMode, GatePolicy

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)
SUMMARY_FILENAME = "static-eval-summary.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MCP static evaluation gates")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="Directory containing normalized scan JSON files",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        required=True,
        help="Directory for the evaluation summary JSON",
    )
    parser.add_argument(
        "--mode",
        choices=[m.value for m in GateMode],
        default=GateMode.BLOCK.value,
        help="Gate enforcement mode (default: block)",
    )
    args = parser.parse_args()
    # Preserve backward compatibility with the original CLI.
    results_dir = args.results_dir or args.reports_dir
    reports_dir = args.reports_dir
    if not results_dir.is_dir():
        logger.error("Not a directory: %s", results_dir)
        return 1
    reports_dir.mkdir(parents=True, exist_ok=True)
    policy = GatePolicy(default_mode=GateMode(args.mode))
    results = run_static_eval(results_dir, policy)
    summary: list[dict] = []
    for result in results:
        gate_key = result.get_policy_key()
        # Store detailed gate results alongside scan results.
        (results_dir / f"{gate_key}-result.json").write_text(result.model_dump_json(indent=2))
        summary.append(
            {
                "check": gate_key,
                "passed": result.passed,
                "status": "pass" if result.passed else "fail",
                "score": result.score,
                "findings": len(result.findings),
                "message": result.message,
            }
        )
        logger.info(
            "%s: passed=%s score=%.2f findings=%d",
            gate_key,
            result.passed,
            result.score,
            len(result.findings),
        )
    overall_passed = all(r.passed for r in results)
    # Write the summary to the reports directory.
    summary_path = reports_dir / SUMMARY_FILENAME
    summary_path.write_text(
        json.dumps(
            {
                "phase": "static-eval",
                "passed": overall_passed,
                "checks": summary,
            },
            indent=2,
        )
    )
    logger.info(
        "Static evaluation overall: %s",
        "PASS" if overall_passed else "FAIL",
    )
    if not overall_passed and args.mode == GateMode.BLOCK.value:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
