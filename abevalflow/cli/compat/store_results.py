"""Command interface for store results."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from abevalflow.persistence.postgres import store


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Persist A/B evaluation results to the database",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        required=True,
        help="Directory containing report.json",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="SQLAlchemy database URL (default: DATABASE_URL env var)",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Pipeline run ID for idempotency (default: content hash of report)",
    )
    args = parser.parse_args()

    ok = store(args.report_dir, args.database_url, args.run_id)
    sys.exit(0 if ok else 1)
