"""Tests for abevalflow/llm_client.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from abevalflow import llm_client
from abevalflow.llm_client import LLMResult


class TestChatCompletionWithUsage:
    def test_returns_llm_result(self) -> None:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello!"
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        mock_client.chat.completions.create.return_value = mock_response

        result = llm_client.chat_completion_with_usage(
            [{"role": "user", "content": "hi"}],
            model="test-model",
            client=mock_client,
        )

        assert isinstance(result, LLMResult)
        assert result.content == "Hello!"
        assert result.prompt_tokens == 100
        assert result.completion_tokens == 50
        assert result.total_tokens == 150
        assert result.model == "test-model"

    def test_handles_no_usage(self) -> None:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello!"
        mock_response.usage = None
        mock_client.chat.completions.create.return_value = mock_response

        result = llm_client.chat_completion_with_usage(
            [{"role": "user", "content": "hi"}],
            model="test-model",
            client=mock_client,
        )

        assert result.content == "Hello!"
        assert result.prompt_tokens == 0
        assert result.completion_tokens == 0
        assert result.total_tokens == 0


try:
    import opentelemetry.sdk.trace  # noqa: F401

    _has_otel_sdk = True
except ImportError:
    _has_otel_sdk = False


@pytest.mark.skipif(not _has_otel_sdk, reason="opentelemetry SDK not installed")
class TestChatCompletionOtelSpan:
    def test_emits_span_with_token_attributes(self) -> None:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello!"
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        mock_client.chat.completions.create.return_value = mock_response

        with patch("abevalflow.observability.otel.get_tracer", return_value=provider.get_tracer("test")):
            result = llm_client.chat_completion_with_usage(
                [{"role": "user", "content": "hi"}],
                model="test-model",
                client=mock_client,
            )

        assert result.prompt_tokens == 100
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "llm.chat_completion"
        attrs = dict(span.attributes)
        assert attrs["llm.model"] == "test-model"
        assert attrs["llm.prompt_tokens"] == 100
        assert attrs["llm.completion_tokens"] == 50
        assert attrs["llm.total_tokens"] == 150
        assert attrs["llm.messages_count"] == 1

        provider.shutdown()

    def test_works_without_otel(self) -> None:
        from abevalflow.observability.otel import _NoOpTracer

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello!"
        mock_response.usage.prompt_tokens = 10
        mock_response.usage.completion_tokens = 5
        mock_client.chat.completions.create.return_value = mock_response

        with patch("abevalflow.observability.otel.get_tracer", return_value=_NoOpTracer()):
            result = llm_client.chat_completion_with_usage(
                [{"role": "user", "content": "hi"}],
                model="test-model",
                client=mock_client,
            )

        assert result.content == "Hello!"
        assert result.prompt_tokens == 10

    def test_records_exception_in_span(self) -> None:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        from opentelemetry.trace import StatusCode

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("API timeout")

        with patch("abevalflow.observability.otel.get_tracer", return_value=provider.get_tracer("test")):
            with pytest.raises(RuntimeError, match="API timeout"):
                llm_client.chat_completion_with_usage(
                    [{"role": "user", "content": "hi"}],
                    model="test-model",
                    client=mock_client,
                )

        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.status.status_code == StatusCode.ERROR
        assert any(e.name == "exception" for e in span.events)

        provider.shutdown()
