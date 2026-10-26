#!/usr/bin/env python3
"""Publish MCP evaluation results to Compass SoundCheck.

Reads phase summary JSON files from ``--reports-dir`` (written by the Phase 1
and Phase 2 gate runners), assembles an :class:`McpEvalResult`, and publishes
facts to the Compass SoundCheck API.

**Dry-run is ON by default.**  Use ``--no-dry-run`` to actually publish.

Usage:
    python -m scripts.mcp.runners.compass_publish \\
        --reports-dir reports/my-server \\
        --entity-ref mcpserver:default/my-server \\
        --compass-url https://compass.stage.redhat.com/api/soundcheck/facts/ \\
        [--no-dry-run]

Environment:
    COMPASS_API_KEY — Bearer token for the SoundCheck API.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from pydantic import ValidationError

from abevalflow.mcp.checks.compass_publish import (
    McpPushConfig,
    push_mcp_eval_facts,
)
from abevalflow.mcp.eval_result import (
    build_mcp_eval_result,
    load_phase1_summary,
    load_phase2_summary,
    load_phase3_summary,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

PHASE1_SUMMARIES = ("static-eval-summary.json",)
PHASE2_SUMMARIES = ("conformance-eval-summary.json",)
PHASE3_SUMMARIES = ("behavioral-eval-summary.json",)


def _summary_path(reports_dir: Path, phase: str, filename: str) -> Path:
    """Find a phase summary in the evaluator's phase subdirectory or flat layout."""
    nested = reports_dir / phase / filename
    if nested.is_file():
        return nested
    return reports_dir / filename


def _phase_summary_path(reports_dir: Path, phase: str, filenames: tuple[str, ...]) -> Path:
    """Find a phase summary by trying filenames in order."""
    for filename in filenames:
        path = _summary_path(reports_dir, phase, filename)
        if path.is_file():
            return path
    return _summary_path(reports_dir, phase, filenames[0])


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish MCP evaluation results to Compass SoundCheck")
    parser.add_argument(
        "--reports-dir",
        type=Path,
        required=True,
        help="Directory holding phase summary JSON files",
    )
    parser.add_argument(
        "--entity-ref",
        required=True,
        help="Compass entity ref (e.g. mcpserver:default/my-server)",
    )
    parser.add_argument(
        "--compass-url",
        required=True,
        help="Compass Facts API URL (e.g. https://compass.stage.redhat.com/api/soundcheck/facts/)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        default=False,
        help="Actually publish to Compass (default is dry-run)",
    )
    args = parser.parse_args()

    if not args.reports_dir.is_dir():
        logger.error("Not a directory: %s", args.reports_dir)
        return 1

    p1_path = _phase_summary_path(args.reports_dir, "static-eval", PHASE1_SUMMARIES)
    p2_path = _phase_summary_path(args.reports_dir, "conformance-eval", PHASE2_SUMMARIES)
    p3_path = _phase_summary_path(args.reports_dir, "behavioral-eval", PHASE3_SUMMARIES)

    if not p1_path.is_file():
        logger.error("Phase 1 summary not found: %s", p1_path)
        return 1
    if not p2_path.is_file():
        logger.error("Phase 2 summary not found: %s", p2_path)
        return 1

    try:
        phase1 = load_phase1_summary(p1_path)
        phase2 = load_phase2_summary(p2_path)
        phase3 = load_phase3_summary(p3_path) if p3_path.is_file() else None
    except (OSError, ValueError, ValidationError) as exc:
        logger.error("Invalid MCP evaluation summary in %s: %s", args.reports_dir, exc)
        return 1

    eval_result = build_mcp_eval_result(phase1, phase2, phase3)

    dry_run = not args.no_dry_run
    api_key = os.environ.get("COMPASS_API_KEY", "")
    if not dry_run and not api_key:
        logger.error("COMPASS_API_KEY not set; cannot publish. Use dry-run or set the variable.")
        return 1

    config = McpPushConfig(
        endpoint=args.compass_url,
        entity_ref=args.entity_ref,
        bearer_token=api_key or None,
        dry_run=dry_run,
    )

    if dry_run:
        logger.info("DRY RUN — payloads will be logged but not sent to Compass")

    results = push_mcp_eval_facts(eval_result, config)

    failed = [r for r in results if not r.success]
    if failed:
        for f in failed:
            logger.error("Failed to push %s: %s", f.fact_ref, f.error)
        return 1

    logger.info(
        "Compass publish %s: %d facts %s for %s",
        "complete" if not dry_run else "dry-run complete",
        len(results),
        "published" if not dry_run else "would be published",
        args.entity_ref,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
