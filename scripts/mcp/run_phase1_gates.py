#!/usr/bin/env python3
"""Run the MCP Phase 1 gates over the normalized scan reports.

Reads the three scan JSON files produced by the Phase 1 scanners, evaluates each
gate, and writes the results back into the reports directory:

- ``<gate>-result.json`` per gate (the full GateResult)
- ``phase1-summary.json`` (overall pass/fail + per-gate summary)

This is the Phase 1 reporting stub. The real Evaluation-results-database writer
(three-state pass/fail/not-evaluated) is added by the pipeline layer later; Phase
1 always executes, so its checks resolve to pass/fail only.

Usage:
    python -m scripts.mcp.run_phase1_gates --reports-dir <dir> [--mode block|warn]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from abevalflow.mcp.phase1 import run_phase1
from abevalflow.schemas import GateMode, GatePolicy

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SUMMARY_FILENAME = "phase1-summary.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MCP Phase 1 gates")
    parser.add_argument(
        "--reports-dir",
        type=Path,
        required=True,
        help="Directory holding the Phase 1 scan JSON files",
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
    results = run_phase1(args.reports_dir, policy)

    summary: list[dict] = []
    for result in results:
        gate_key = result.get_policy_key()
        (args.reports_dir / f"{gate_key}-result.json").write_text(result.model_dump_json(indent=2))
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
            {"phase": "phase1", "passed": overall_passed, "gates": summary},
            indent=2,
        )
    )

    logger.info("Phase 1 overall: %s", "PASS" if overall_passed else "FAIL")
    # Exit non-zero in block mode when any gate failed, so the Tekton step fails.
    if not overall_passed and args.mode == GateMode.BLOCK.value:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
