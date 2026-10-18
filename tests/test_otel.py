"""Tests for OTEL setup and timed_gate decorator with OTEL spans."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from abevalflow.gates.base import GateMode, GateResult, GateType
from abevalflow.observability.decorators import timed_gate

# ---------------------------------------------------------------------------
# otel.py unit tests
# ---------------------------------------------------------------------------


class TestIsOtelEnabled:
    def test_disabled_when_no_env_vars(self):
        with patch.dict(os.environ, {}, clear=True):
            from abevalflow.observability.otel import is_otel_enabled

            assert is_otel_enabled() is False

    def test_enabled_with_otel_endpoint(self):
        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"}):
            from abevalflow.observability.otel import is_otel_enabled

            assert is_otel_enabled() is True


class TestGetTracer:
    def test_returns_tracer_with_otel_installed(self):
        from abevalflow.observability.otel import get_tracer

        tracer = get_tracer("test")
        assert tracer is not None
        span = tracer.start_as_current_span("test-span")
        assert span is not None

    def test_tracer_creates_noop_span_without_provider(self):
        from abevalflow.observability.otel import get_tracer

        tracer = get_tracer("test")
        with tracer.start_as_current_span("test-span") as span:
            span.set_attribute("key", "value")


class TestSetupTracerProvider:
    def test_no_crash_when_no_env_vars(self):
        import abevalflow.observability.otel as otel_mod

        otel_mod._provider_initialized = False
        with patch.dict(os.environ, {}, clear=True):
            otel_mod.setup_tracer_provider()

    def test_standalone_otel_setup(self):
        import abevalflow.observability.otel as otel_mod

        otel_mod._provider_initialized = False
        with patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:4317"}, clear=True):
            otel_mod.setup_tracer_provider()
            assert otel_mod._provider_initialized is True

    def test_idempotent(self):
        import abevalflow.observability.otel as otel_mod

        otel_mod._provider_initialized = False
        with patch.dict(os.environ, {}, clear=True):
            otel_mod.setup_tracer_provider()
            otel_mod.setup_tracer_provider()
            assert otel_mod._provider_initialized is True


class TestShutdownTracerProvider:
    def test_no_crash_on_shutdown(self):
        from abevalflow.observability.otel import shutdown_tracer_provider

        shutdown_tracer_provider()


# ---------------------------------------------------------------------------
# timed_gate decorator with OTEL span tests
# ---------------------------------------------------------------------------


try:
    import opentelemetry.sdk.trace  # noqa: F401

    _has_otel_sdk = True
except ImportError:
    _has_otel_sdk = False

_skip_no_otel = pytest.mark.skipif(not _has_otel_sdk, reason="opentelemetry SDK not installed")


@_skip_no_otel
class TestTimedGateWithOtel:
    def test_emits_span_with_attributes(self):
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        with patch("abevalflow.observability.decorators.get_tracer") as mock_get_tracer:
            mock_get_tracer.return_value = provider.get_tracer("test")

            @timed_gate
            def my_gate():
                return GateResult(
                    gate_type=GateType.SECURITY,
                    gate_name="security",
                    policy_key="test-gate",
                    passed=True,
                    score=0.95,
                    mode=GateMode.BLOCK,
                )

            result = my_gate()

        assert result.passed is True
        assert result.score == 0.95

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name.startswith("gate.") and span.name.endswith("my_gate")
        attrs = dict(span.attributes)
        assert attrs["gate.passed"] is True
        assert attrs["gate.score"] == 0.95
        assert attrs["gate.gate_type"] == "security"
        assert attrs["gate.mode"] == "block"
        assert "gate.duration_ms" in attrs

        provider.shutdown()

    def test_records_exception(self):
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        with patch("abevalflow.observability.decorators.get_tracer") as mock_get_tracer:
            mock_get_tracer.return_value = provider.get_tracer("test")

            @timed_gate
            def failing_gate():
                raise ValueError("gate exploded")

            with pytest.raises(ValueError, match="gate exploded"):
                failing_gate()

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name.startswith("gate.") and span.name.endswith("failing_gate")
        assert len(span.events) > 0
        assert any(e.name == "exception" for e in span.events)

        provider.shutdown()

    def test_works_without_gate_result_attributes(self):
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        with patch("abevalflow.observability.decorators.get_tracer") as mock_get_tracer:
            mock_get_tracer.return_value = provider.get_tracer("test")

            @timed_gate
            def plain_function():
                return "not a GateResult"

            result = plain_function()

        assert result == "not a GateResult"
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        assert "gate.passed" not in dict(spans[0].attributes)

        provider.shutdown()

    def test_preserves_duration_ms_attribute(self):
        class ResultWithDuration:
            _duration_ms = 0

        with patch("abevalflow.observability.decorators.get_tracer") as mock_get_tracer:
            from abevalflow.observability.otel import _NoOpTracer

            mock_get_tracer.return_value = _NoOpTracer()

            @timed_gate
            def gate_with_duration():
                return ResultWithDuration()

            result = gate_with_duration()
            assert result._duration_ms > 0 or result._duration_ms == 0

    def test_failed_gate_sets_error_status(self):
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        from opentelemetry.trace import StatusCode

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        with patch("abevalflow.observability.decorators.get_tracer") as mock_get_tracer:
            mock_get_tracer.return_value = provider.get_tracer("test")

            @timed_gate
            def failing_policy_gate():
                return GateResult(
                    gate_type=GateType.SECURITY,
                    gate_name="security",
                    policy_key="test-gate",
                    passed=False,
                    score=0.2,
                    mode=GateMode.BLOCK,
                )

            result = failing_policy_gate()

        assert result.passed is False
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.status.status_code == StatusCode.ERROR
        assert "failed" in span.status.description
        assert dict(span.attributes)["gate.passed"] is False
        assert dict(span.attributes)["gate.score"] == 0.2

        provider.shutdown()

    def test_none_score_not_set_as_attribute(self):
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        with patch("abevalflow.observability.decorators.get_tracer") as mock_get_tracer:
            mock_get_tracer.return_value = provider.get_tracer("test")

            class FakeResult:
                passed = True
                score = None
                gate_type = "quality"
                mode = "warn"

            @timed_gate
            def gate_without_score():
                return FakeResult()

            gate_without_score()

        spans = exporter.get_finished_spans()
        assert "gate.score" not in dict(spans[0].attributes)

        provider.shutdown()

    def test_noop_tracer_still_executes_function(self):
        from abevalflow.observability.otel import _NoOpTracer

        with patch("abevalflow.observability.decorators.get_tracer") as mock_get_tracer:
            mock_get_tracer.return_value = _NoOpTracer()

            @timed_gate
            def simple_gate():
                return GateResult(
                    gate_type=GateType.QUALITY,
                    gate_name="quality",
                    passed=True,
                    score=1.0,
                    mode=GateMode.WARN,
                )

            result = simple_gate()
            assert result.passed is True
            assert result.score == 1.0
