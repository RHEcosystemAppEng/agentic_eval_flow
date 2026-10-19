"""Tests for abevalflow.observability.context — MetricsContext, TokenUsage, TimingRecord."""

from __future__ import annotations

import json
from pathlib import Path

from abevalflow.observability.context import CHECKPOINT_FILENAME, MetricsContext, TimingRecord, TokenUsage


class TestTokenUsage:
    def test_defaults(self) -> None:
        t = TokenUsage()
        assert t.prompt_tokens == 0
        assert t.completion_tokens == 0
        assert t.total_tokens == 0
        assert t.call_count == 0

    def test_accumulate(self) -> None:
        t = TokenUsage()
        t.accumulate(100, 50, "claude-sonnet")
        assert t.prompt_tokens == 100
        assert t.completion_tokens == 50
        assert t.total_tokens == 150
        assert t.call_count == 1
        assert t.model_name == "claude-sonnet"

    def test_accumulate_multiple(self) -> None:
        t = TokenUsage()
        t.accumulate(100, 50, "claude-sonnet")
        t.accumulate(200, 80, "claude-sonnet")
        assert t.prompt_tokens == 300
        assert t.completion_tokens == 130
        assert t.total_tokens == 430
        assert t.call_count == 2


class TestTimingRecord:
    def test_stop_computes_duration(self) -> None:
        rec = TimingRecord(name="test", start_time=1000.0)
        rec.end_time = 1002.5
        rec.duration_ms = int((rec.end_time - rec.start_time) * 1000)
        assert rec.duration_ms == 2500

    def test_stop_method(self) -> None:
        import time

        rec = TimingRecord(name="test", start_time=time.time())
        rec.stop()
        assert rec.end_time is not None
        assert rec.duration_ms is not None
        assert rec.duration_ms >= 0


class TestMetricsContext:
    def test_record_tokens(self) -> None:
        ctx = MetricsContext(run_id="run-1", submission_name="test-skill")
        ctx.record_tokens("quality_review", 500, 200, "claude-sonnet")
        assert ctx.total_prompt_tokens == 500
        assert ctx.total_completion_tokens == 200
        assert ctx.total_tokens == 700
        assert ctx.llm_calls_count == 1
        assert ctx.model_name == "claude-sonnet"

    def test_record_tokens_multiple_phases(self) -> None:
        ctx = MetricsContext(run_id="run-1", submission_name="test-skill")
        ctx.record_tokens("quality_review", 500, 200, "claude-sonnet")
        ctx.record_tokens("security_scan", 300, 100, "claude-sonnet")
        assert ctx.total_prompt_tokens == 800
        assert ctx.total_completion_tokens == 300
        assert ctx.total_tokens == 1100
        assert ctx.llm_calls_count == 2

    def test_record_tokens_same_phase(self) -> None:
        ctx = MetricsContext(run_id="run-1", submission_name="test-skill")
        ctx.record_tokens("quality_review", 500, 200)
        ctx.record_tokens("quality_review", 300, 150)
        assert ctx.token_usage["quality_review"].prompt_tokens == 800
        assert ctx.token_usage["quality_review"].call_count == 2
        assert ctx.total_tokens == 1150

    def test_timing(self) -> None:
        ctx = MetricsContext()
        ctx.timings["test"] = TimingRecord(name="test", start_time=1000.0, end_time=1005.0, duration_ms=5000)
        assert ctx.timing_ms("test") == 5000
        assert ctx.timing_ms("missing") is None

    def test_start_and_stop_timing(self) -> None:
        ctx = MetricsContext()
        ctx.stop_timing("missing")  # Stopping an unknown phase is a safe no-op.
        ctx.start_timing("prepare")
        ctx.stop_timing("prepare")
        assert ctx.timing_ms("prepare") is not None
        assert ctx.timing_ms("prepare") >= 0

    def test_checkpoint_round_trip(self, tmp_path: Path) -> None:
        ctx = MetricsContext(run_id="run-1", submission_name="test-skill", model_name="claude-sonnet")
        ctx.record_tokens("quality_review", 500, 200, "claude-sonnet")
        ctx.timings["test"] = TimingRecord(name="test", start_time=1000.0, end_time=1005.0, duration_ms=5000)
        ctx.checkpoint(tmp_path)

        loaded = MetricsContext.load_checkpoint(tmp_path)
        assert loaded is not None
        assert loaded.run_id == "run-1"
        assert loaded.submission_name == "test-skill"
        assert loaded.total_tokens == 700
        assert loaded.timing_ms("test") == 5000

    def test_checkpoint_file_written(self, tmp_path: Path) -> None:
        ctx = MetricsContext(run_id="run-1")
        ctx.checkpoint(tmp_path)
        path = tmp_path / CHECKPOINT_FILENAME
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["run_id"] == "run-1"

    def test_load_checkpoint_missing(self, tmp_path: Path) -> None:
        result = MetricsContext.load_checkpoint(tmp_path)
        assert result is None

    def test_load_checkpoint_invalid(self, tmp_path: Path) -> None:
        path = tmp_path / CHECKPOINT_FILENAME
        path.write_text("not valid json{{{")
        result = MetricsContext.load_checkpoint(tmp_path)
        assert result is None

    def test_to_observability_dict(self) -> None:
        ctx = MetricsContext(run_id="run-1", submission_name="test-skill", model_name="claude-sonnet")
        ctx.record_tokens("quality_review", 500, 200, "claude-sonnet")
        ctx.timings["pipeline"] = TimingRecord(name="pipeline", start_time=0, end_time=10, duration_ms=10000)
        ctx.timings["test"] = TimingRecord(name="test", start_time=0, end_time=5, duration_ms=5000)

        d = ctx.to_observability_dict()
        assert d["submission_name"] == "test-skill"
        assert d["model_name"] == "claude-sonnet"
        assert d["total_prompt_tokens"] == 500
        assert d["total_completion_tokens"] == 200
        assert d["total_tokens"] == 700
        assert d["pipeline_duration_ms"] == 10000
        assert d["test_duration_ms"] == 5000
        assert d["evaluate_duration_ms"] is None
        assert d["llm_calls_count"] == 1

    def test_empty_context_observability_dict(self) -> None:
        ctx = MetricsContext()
        d = ctx.to_observability_dict()
        assert d["total_prompt_tokens"] is None
        assert d["total_completion_tokens"] is None
        assert d["total_tokens"] is None
        assert d["llm_calls_count"] is None
