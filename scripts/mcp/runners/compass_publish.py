#!/usr/bin/env python3
"""Publish supplied MCP phase summaries to Compass SoundCheck.

Pass explicit summary files with ``--summary-files`` or a scoped report
directory with ``--reports-dir``. Directory discovery stays inside that path.

**Dry-run is ON by default.** Use ``--no-dry-run`` to actually publish.

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

from abevalflow.mcp.checks.compass_publish import McpPushConfig, push_mcp_eval_facts
from abevalflow.mcp.eval_result import build_mcp_eval_result, load_phase_summary, validate_phase_name

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SUMMARY_SUFFIX = "-summary.json"


def _phase_from_filename(path: Path) -> str:
    """Extract phase name from ``<phase-name>-summary.json``."""
    filename = path.name
    if not filename.endswith(SUMMARY_SUFFIX):
        raise ValueError(f"Summary filename must end with {SUMMARY_SUFFIX!r}: {filename}")
    phase_name = filename[: -len(SUMMARY_SUFFIX)]
    try:
        return validate_phase_name(phase_name)
    except ValueError as exc:
        raise ValueError(f"Invalid phase name in summary filename: {filename}") from exc


def _summary_files(summary_files: list[Path] | None, reports_dir: Path | None) -> list[Path]:
    """Resolve explicit files or discover summaries inside the supplied directory."""
    if summary_files is not None:
        files = summary_files
    elif reports_dir is not None:
        if not reports_dir.is_dir():
            raise ValueError(f"Not a directory: {reports_dir}")
        files = sorted(reports_dir.rglob(f"*{SUMMARY_SUFFIX}"))
    else:
        raise ValueError("Provide --summary-files or --reports-dir")

    if not files:
        raise ValueError("No phase summary files found")
    for path in files:
        if not path.is_file():
            raise ValueError(f"Summary file not found: {path}")
    return files


def _load_summaries(files: list[Path]):
    phases = []
    seen: set[str] = set()
    for path in files:
        filename_phase = _phase_from_filename(path)
        phase_result = load_phase_summary(path)
        if phase_result.phase != filename_phase:
            raise ValueError(
                f"Phase mismatch for {path}: filename says {filename_phase!r}, JSON says {phase_result.phase!r}"
            )
        if phase_result.phase in seen:
            raise ValueError(f"Duplicate phase summary for {phase_result.phase!r}")
        seen.add(phase_result.phase)
        phases.append(phase_result)
    return build_mcp_eval_result(phases)


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish MCP phase summaries to Compass SoundCheck")
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "--summary-files",
        type=Path,
        nargs="+",
        help="Explicit phase summary paths named <phase-name>-summary.json",
    )
    input_group.add_argument(
        "--reports-dir",
        type=Path,
        help="Scoped report directory; recursively discovers *-summary.json files beneath it",
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

    try:
        files = _summary_files(args.summary_files, args.reports_dir)
        eval_result = _load_summaries(files)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.error("Invalid phase summary input: %s", exc)
        return 1

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
        for result in failed:
            logger.error("Failed to push %s: %s", result.fact_ref, result.error)
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
