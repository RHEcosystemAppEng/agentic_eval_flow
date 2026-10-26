"""Metrics context for accumulating token usage and timing across a pipeline run.

Append-only during execution, then serialized and persisted at the end.
Per-gate token buckets keyed by phase/gate name.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

CHECKPOINT_FILENAME = "_metrics_checkpoint.json"


class TokenUsage(BaseModel):
    """Token counts for a single phase or gate."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model_name: str | None = None
    call_count: int = 0

    def accumulate(self, prompt: int, completion: int, model: str | None = None) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += prompt + completion
        self.call_count += 1
        if model:
            self.model_name = model


class TimingRecord(BaseModel):
    """Duration record for a pipeline phase."""

    name: str
    start_time: float = Field(description="Unix timestamp")
    end_time: float | None = None
    duration_ms: int | None = None

    def stop(self) -> None:
        self.end_time = time.time()
        self.duration_ms = int((self.end_time - self.start_time) * 1000)


class MetricsContext(BaseModel):
    """Accumulates token usage and timing across a pipeline run.

    Token usage is bucketed by phase/gate name to support concurrent execution.
    """

    run_id: str = ""
    submission_name: str = ""
    model_name: str | None = None
    start_time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    timings: dict[str, TimingRecord] = Field(default_factory=dict)
    token_usage: dict[str, TokenUsage] = Field(default_factory=dict)

    def record_tokens(
        self,
        phase_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        model: str | None = None,
    ) -> None:
        if phase_name not in self.token_usage:
            self.token_usage[phase_name] = TokenUsage()
        self.token_usage[phase_name].accumulate(prompt_tokens, completion_tokens, model)
        if model and not self.model_name:
            self.model_name = model

    def start_timing(self, phase_name: str) -> None:
        self.timings[phase_name] = TimingRecord(name=phase_name, start_time=time.time())

    def stop_timing(self, phase_name: str) -> None:
        if phase_name in self.timings:
            self.timings[phase_name].stop()

    @property
    def total_prompt_tokens(self) -> int:
        return sum(u.prompt_tokens for u in self.token_usage.values())

    @property
    def total_completion_tokens(self) -> int:
        return sum(u.completion_tokens for u in self.token_usage.values())

    @property
    def total_tokens(self) -> int:
        return sum(u.total_tokens for u in self.token_usage.values())

    @property
    def llm_calls_count(self) -> int:
        return sum(u.call_count for u in self.token_usage.values())

    def timing_ms(self, phase_name: str) -> int | None:
        rec = self.timings.get(phase_name)
        return rec.duration_ms if rec else None

    def checkpoint(self, workspace_path: Path) -> None:
        path = workspace_path / CHECKPOINT_FILENAME
        path.write_text(self.model_dump_json(indent=2))
        logger.info("Metrics checkpoint written to %s", path)

    @classmethod
    def load_checkpoint(cls, workspace_path: Path) -> MetricsContext | None:
        path = workspace_path / CHECKPOINT_FILENAME
        if not path.exists():
            return None
        try:
            return cls.model_validate_json(path.read_bytes())
        except Exception:
            logger.warning("Failed to load metrics checkpoint from %s", path, exc_info=True)
            return None

    def to_observability_dict(self) -> dict:
        """Convert to kwargs for ObservabilityMetricsRow."""
        from abevalflow.observability.cost import calculate_cost

        prompt = self.total_prompt_tokens
        completion = self.total_completion_tokens

        return {
            "submission_name": self.submission_name,
            "model_name": self.model_name,
            "pipeline_duration_ms": self.timing_ms("pipeline"),
            "prepare_duration_ms": self.timing_ms("prepare"),
            "test_duration_ms": self.timing_ms("test"),
            "evaluate_duration_ms": self.timing_ms("evaluate"),
            "analyze_duration_ms": self.timing_ms("analyze"),
            "store_duration_ms": self.timing_ms("store"),
            "total_prompt_tokens": prompt or None,
            "total_completion_tokens": completion or None,
            "total_tokens": self.total_tokens or None,
            "estimated_cost_usd": calculate_cost(prompt, completion, self.model_name),
            "llm_calls_count": self.llm_calls_count or None,
        }
