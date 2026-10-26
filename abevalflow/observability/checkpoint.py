"""Record review token usage in the shared metrics checkpoint format."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

from abevalflow.observability.context import MetricsContext


def checkpoint_review(run_id: str, name: str, reports: Path, assessment: dict) -> None:
    """Persist available token usage without requiring a separate bookkeeping process."""
    usage = assessment.get("token_usage", {})
    context = MetricsContext.load_checkpoint(reports) or MetricsContext(run_id=run_id, submission_name=name)
    if usage:
        context.record_tokens(
            "quality_review", usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), usage.get("model")
        )
    if context.total_tokens:
        reports.mkdir(parents=True, exist_ok=True)
        context.checkpoint(reports)


def checkpoint_review_file(run_id: str, name: str, reports: Path, review_file: Path | None) -> None:
    """Read an optional advisory review and record its token usage."""
    import json

    if review_file is None or not review_file.is_file():
        return
    try:
        assessment = json.loads(review_file.read_text())
        checkpoint_review(run_id, name, reports, assessment)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("Could not read review tokens: %s", exc)
