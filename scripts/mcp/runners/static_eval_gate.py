#!/usr/bin/env python3
"""Run the MCP static evaluation gates over the normalized scan reports.

Reads the three scan JSON files produced by the static evaluation scanners, evaluates
each gate, and writes results:

- ``<gate>-result.json`` per gate (the full GateResult) → results directory
- ``static-eval-summary.json`` (overall pass/fail + per-gate summary) → reports directory

Reporting stub: the three-state Evaluation-results-database writer is added by the
pipeline layer later; static evaluation checks are pass/fail only.

Usage:
    python -m scripts.mcp.runners.static_eval_gate --results-dir <dir> --reports-dir <dir> [--mode block|warn]
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
        required=True,
        help="Directory holding the static evaluation scan JSON files (input)",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        required=True,
        help="Directory for writing the summary JSON file (output)",
    )
    parser.add_argument(
        "--mode",
        choices=[m.value for m in GateMode],
        default=GateMode.BLOCK.value,
        help="Gate enforcement mode (default: block)",
    )
    args = parser.parse_args()

    if not args.results_dir.is_dir():
        logger.error("Not a directory: %s", args.results_dir)
        return 1

    args.reports_dir.mkdir(parents=True, exist_ok=True)

    policy = GatePolicy(default_mode=GateMode(args.mode))
    results = run_static_eval(args.results_dir, policy)

    summary: list[dict] = []
    for result in results:
        gate_key = result.get_policy_key()
        (args.results_dir / f"{gate_key}-result.json").write_text(result.model_dump_json(indent=2))
        summary.append(
            {
                "gate": gate_key,
                "passed": result.passed,
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
    (args.reports_dir / SUMMARY_FILENAME).write_text(
        json.dumps(
            {"phase": "static-eval", "passed": overall_passed, "gates": summary},
            indent=2,
        )
    )

    logger.info("Static evaluation overall: %s", "PASS" if overall_passed else "FAIL")
    # Exit non-zero in block mode when any gate failed, so the Tekton step fails.
    if not overall_passed and args.mode == GateMode.BLOCK.value:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
