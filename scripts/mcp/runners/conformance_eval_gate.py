#!/usr/bin/env python3
"""Run the MCP conformance evaluation gates over the three-state check result files.

Reads the ``<check>-check.json`` files produced by the conformance evaluation probe
and the Compass-consume step, evaluates each gate, and writes results:

- ``<check>-result.json`` per gate (the full GateResult) → results directory
- ``conformance-eval-summary.json`` (overall pass/fail + per-check summary) → reports directory

Three-state aware: a ``not_evaluated`` check never fails the evaluation. The step
fails (non-zero exit) only when a check genuinely FAILED in block mode. Reporting
to the Evaluation-results database is still handled by the pipeline layer later;
this writes local JSON only.

Usage:
    python -m scripts.mcp.runners.conformance_eval_gate --results-dir <dir> --reports-dir <dir> [--mode block|warn]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from abevalflow.mcp.gates import run_conformance_eval
from abevalflow.schemas import GateMode, GatePolicy

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SUMMARY_FILENAME = "conformance-eval-summary.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MCP conformance evaluation gates")
    parser.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help="Directory holding the conformance evaluation check result files (input)",
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
    results = run_conformance_eval(args.results_dir, policy)

    summary: list[dict] = []
    for result in results:
        check_key = result.get_policy_key()
        status = result.details.get("status", "unknown")
        (args.results_dir / f"{check_key}-result.json").write_text(result.model_dump_json(indent=2))
        summary.append(
            {
                "check": check_key,
                "status": status,
                "passed": result.passed,
                "score": result.score,
                "findings": len(result.findings),
                "source": result.details.get("source"),
                "message": result.message,
            }
        )
        logger.info("%s: status=%s passed=%s score=%.2f", check_key, status, result.passed, result.score)

    overall_passed = all(r.passed for r in results)
    counts = {
        "pass": sum(1 for s in summary if s["status"] == "pass"),
        "fail": sum(1 for s in summary if s["status"] == "fail"),
        "not_evaluated": sum(1 for s in summary if s["status"] == "not_evaluated"),
    }
    (args.reports_dir / SUMMARY_FILENAME).write_text(
        json.dumps(
            {"phase": "conformance-eval", "passed": overall_passed, "counts": counts, "checks": summary}, indent=2
        )
    )

    logger.info("Conformance evaluation overall: %s (%s)", "PASS" if overall_passed else "FAIL", counts)
    # A gate is only not-passed when a real check FAILED in block mode
    # (not_evaluated and warn-mode failures keep passed=True), so this exits
    # non-zero exactly when the step should fail.
    return 0 if overall_passed else 1


if __name__ == "__main__":
    sys.exit(main())
