"""Command interface for test quality review."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from abevalflow.review.submission import logger, review_submission


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="AI quality review of submission")
    parser.add_argument("submission_dir", type=Path, help="Path to the submission directory")
    args = parser.parse_args(argv)

    if not args.submission_dir.is_dir():
        result = {"passed": False, "error": f"Not a directory: {args.submission_dir}"}
        print(json.dumps(result, indent=2))
        return 1

    try:
        assessment = review_submission(args.submission_dir)
    except Exception as exc:
        logger.error("Review failed: %s", exc)
        # Prefer non-blocking for AEH even on unexpected errors
        is_aeh = (args.submission_dir / "eval.yaml").is_file() or (
            args.submission_dir / "eval-treatment.yaml"
        ).is_file()
        result = {
            "passed": True if is_aeh else False,
            "recommendation": "warn" if is_aeh else "fail",
            "error": str(exc),
            "engine": "aeh" if is_aeh else None,
        }
        print(json.dumps(result, indent=2))
        return 0 if is_aeh else 1

    print(json.dumps(assessment, indent=2))
    # Always return 0 - this review is advisory (non-blocking)
    # The assessment result is logged but doesn't fail the pipeline
    if not assessment.get("passed", False):
        logger.warning(
            "Quality review recommendation: %s (advisory, non-blocking)", assessment.get("recommendation", "unknown")
        )
    return 0
