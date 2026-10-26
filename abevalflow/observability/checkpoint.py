"""Record review token usage in the shared metrics checkpoint format."""

from pathlib import Path

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
