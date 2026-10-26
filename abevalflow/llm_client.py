"""Explicitly configured completions with token accounting and optional tracing."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI


@dataclass
class LLMResult:
    """Chat completion result with token usage."""

    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    model: str


logger = logging.getLogger(__name__)


def _set_span_status_error(span, description: str):
    """Set span status to ERROR, handling missing opentelemetry gracefully."""
    try:
        from opentelemetry.trace import StatusCode

        span.set_status(StatusCode.ERROR, description)
    except ImportError:
        pass


def chat_completion_with_usage(
    messages: list[dict[str, str]],
    *,
    client: OpenAI,
    model: str,
    temperature: float | None = 0.3,
    max_tokens: int = 4096,
    **kwargs,
) -> LLMResult:
    """Send a chat completion request and return content with token usage.

    Raises on API errors so callers can handle retries at a higher level.
    """
    from abevalflow.observability.otel import get_tracer

    tracer = get_tracer("abevalflow.llm")

    with tracer.start_as_current_span(
        "llm.chat_completion",
        attributes={
            key: value
            for key, value in {
                "llm.model": model,
                "llm.temperature": temperature,
                "llm.max_tokens": max_tokens,
                "llm.messages_count": len(messages),
            }.items()
            if value is not None
        },
    ) as span:
        logger.info(
            "chat_completion → model=%s, messages=%d, temperature=%s",
            model,
            len(messages),
            temperature,
        )

        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                **({"temperature": temperature} if temperature is not None else {}),
                **kwargs,
            )
        except Exception as exc:
            span.record_exception(exc)
            _set_span_status_error(span, str(exc))
            raise

        content = response.choices[0].message.content or ""
        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0

        span.set_attribute("llm.prompt_tokens", prompt_tokens)
        span.set_attribute("llm.completion_tokens", completion_tokens)
        span.set_attribute("llm.total_tokens", prompt_tokens + completion_tokens)

        logger.info(
            "chat_completion ← %d chars, tokens: %d prompt + %d completion",
            len(content),
            prompt_tokens,
            completion_tokens,
        )

        return LLMResult(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            model=model,
        )
