#!/usr/bin/env python3
"""Run the MCP Phase 2 gates over the three-state check result files.

Reads the ``<check>-check.json`` files produced by the Phase 2 probe and the
Compass-consume step, evaluates each gate, and writes results into the reports
directory:

- ``<check>-result.json`` per gate (the full GateResult)
- ``phase2-summary.json`` (overall pass/fail + per-check summary with status)

Three-state aware: a ``not_evaluated`` check never fails the phase. The step
fails (non-zero exit) only when a check genuinely FAILED in block mode. Reporting
to the Evaluation-results database is still handled by the pipeline layer later;
this writes local JSON only.

Usage:
    python -m scripts.mcp.run_phase2_gates --reports-dir <dir> [--mode block|warn]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from abevalflow.mcp.phase2 import run_phase2
from abevalflow.schemas import GateMode, GatePolicy

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SUMMARY_FILENAME = "phase2-summary.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MCP Phase 2 gates")
    parser.add_argument(
        "--reports-dir", type=Path, required=True, help="Directory holding the Phase 2 check result files"
    )
    parser.add_argument(
        "--mode",
        choices=[m.value for m in GateMode],
        default=GateMode.BLOCK.value,
        help="Gate enforcement mode (default: block)",
    )
    args = parser.parse_args()

    if not args.reports_dir.is_dir():
        logger.error("Not a directory: %s", args.reports_dir)
        return 1

    policy = GatePolicy(default_mode=GateMode(args.mode))
    results = run_phase2(args.reports_dir, policy)

    summary: list[dict] = []
    for result in results:
        check_key = result.get_policy_key()
        status = result.details.get("status", "unknown")
        (args.reports_dir / f"{check_key}-result.json").write_text(result.model_dump_json(indent=2))
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
        json.dumps({"phase": "phase2", "passed": overall_passed, "counts": counts, "checks": summary}, indent=2)
    )

    logger.info("Phase 2 overall: %s (%s)", "PASS" if overall_passed else "FAIL", counts)
    # A gate is only not-passed when a real check FAILED in block mode
    # (not_evaluated and warn-mode failures keep passed=True), so this exits
    # non-zero exactly when the step should fail.
    return 0 if overall_passed else 1


if __name__ == "__main__":
    sys.exit(main())
