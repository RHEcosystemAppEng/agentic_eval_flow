#!/usr/bin/env python3
"""MCP Phase 2 Compass-consumed checks — CLI entry point.

Reads Compass SoundCheck facts from a local JSON file and maps them into the
three-state per-check result files the Phase 2 gates read.

Mapping logic lives in ``abevalflow.mcp.phase2.compass``; this module is the
thin CLI wrapper that reads the facts source and writes results.

Facts are supplied from a local JSON file. Live SoundCheck retrieval is not wired
yet: the read endpoint and access permissions are still being settled with the
Compass team, so ``--facts-file`` is the only path until then.

Usage:
    python -m scripts.mcp.run_compass_fetch --facts-file facts.json --reports-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from abevalflow.mcp.phase2.compass import map_facts
from abevalflow.mcp.types import write_check_result

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP Phase 2 Compass-consumed checks")
    parser.add_argument(
        "--reports-dir", type=Path, required=True, help="Directory to write <check>-check.json files into"
    )
    parser.add_argument("--facts-file", type=Path, required=True, help="Local JSON file of Compass facts")
    args = parser.parse_args()

    facts: dict[str, Any] = {}
    if not args.facts_file.is_file():
        logger.warning("Facts file not found: %s; consumed checks reported not_evaluated.", args.facts_file)
    else:
        try:
            loaded = json.loads(args.facts_file.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Could not read facts file %s (%s); consumed checks reported not_evaluated.", args.facts_file, exc
            )
        else:
            if isinstance(loaded, dict):
                facts = loaded
            else:
                logger.warning(
                    "Facts file %s is not a JSON object; consumed checks reported not_evaluated.", args.facts_file
                )

    outcomes = map_facts(facts)
    for outcome in outcomes:
        write_check_result(args.reports_dir, outcome)
    logger.info("Compass consume complete: %d checks", len(outcomes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
