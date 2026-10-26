"""Serialization of scorecards and standalone certification artifacts."""

import json
import logging
from pathlib import Path

from abevalflow.scorecard import Scorecard

logger = logging.getLogger(__name__)


def write_scorecard(scorecard: Scorecard, reports_dir: Path) -> Path:
    """Write scorecard to JSON file."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_path = reports_dir / "scorecard.json"

    with open(output_path, "w") as f:
        f.write(scorecard.model_dump_json(indent=2))

    logger.info("Wrote scorecard to %s", output_path)
    return output_path


def write_certification(scorecard: Scorecard, reports_dir: Path) -> Path | None:
    """Write standalone certification.json file.

    Creates a separate certification.json alongside scorecard.json for easier
    consumption by systems that only need certification status.

    Returns:
        Path to certification.json, or None if no certification data.
    """
    if scorecard.certification is None:
        logger.info("No certification data to write")
        return None

    reports_dir.mkdir(parents=True, exist_ok=True)
    output_path = reports_dir / "certification.json"

    def _level_data(level_result):
        failed_checks = [c.check_id.value for c in level_result.checks if not c.passed]
        return {
            "passed": level_result.passed,
            "checks_total": level_result.checks_total,
            "checks_passed": level_result.checks_passed,
            "checks": [c.model_dump() for c in level_result.checks],
            "failed_checks": failed_checks,
        }

    cert_data = {
        "submission_name": scorecard.submission_name,
        "pipeline_run_id": scorecard.pipeline_run_id,
        "highest_level": scorecard.highest_certification.value,
        "levels": {
            "foundational": _level_data(scorecard.certification.foundational),
            "trusted": _level_data(scorecard.certification.trusted),
            "certified": _level_data(scorecard.certification.certified),
        },
        "created_at": scorecard.created_at.isoformat(),
    }

    with open(output_path, "w") as f:
        json.dump(cert_data, f, indent=2)

    logger.info("Wrote certification to %s", output_path)
    return output_path
