"""Command interface for print compass facts debug."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from abevalflow.reporting.compass import print_compass_facts_debug


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        print_compass_facts_debug(args.report_dir)
    except Exception as e:
        print(f"WARNING: could not reconstruct Compass Facts payloads for logging: {e}")
    return 0
