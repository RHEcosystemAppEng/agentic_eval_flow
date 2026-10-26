"""Command interface for write metrics checkpoint."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from abevalflow.observability.checkpoint import checkpoint_review_file


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Write metrics checkpoint")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--submission-name", required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--review-file", type=Path, default=None)
    args = parser.parse_args()

    checkpoint_review_file(args.run_id, args.submission_name, args.report_dir, args.review_file)
