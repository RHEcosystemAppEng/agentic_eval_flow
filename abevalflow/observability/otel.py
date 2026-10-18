"""Optional OTLP tracing for scorecard gates and LLM calls."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_provider_initialized = False


def is_otel_enabled() -> bool:
    """Whether an OTLP endpoint is configured."""
    return bool(os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"))


def setup_tracer_provider() -> None:
    """Configure OTLP tracing once when its endpoint is provided."""
    global _provider_initialized
    if _provider_initialized:
        return

    otel_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")

    if not otel_endpoint:
        _provider_initialized = True
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create(
            {
                "service.name": os.environ.get("OTEL_SERVICE_NAME", "abevalflow"),
            }
        )
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=otel_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        logger.info("OTEL: standalone TracerProvider configured (endpoint=%s)", otel_endpoint)
    except ImportError:
        logger.warning("OTEL: opentelemetry packages not installed — tracing disabled")

    _provider_initialized = True


def shutdown_tracer_provider() -> None:
    """Flush pending spans and shut down the TracerProvider."""
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
    except ImportError:
        pass


def get_tracer(name: str = "abevalflow"):
    """Return a tracer instance (real or no-op).

    Returns the OTEL API no-op tracer if opentelemetry is not installed
    or not configured, so callers never need to check availability.
    """
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except ImportError:
        return _NoOpTracer()


class _NoOpSpan:
    """Minimal no-op span for when opentelemetry is not installed."""

    def set_attribute(self, key, value):
        pass

    def set_status(self, *args, **kwargs):
        pass

    def record_exception(self, exception):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _NoOpTracer:
    """Minimal no-op tracer for when opentelemetry is not installed."""

    def start_as_current_span(self, name, **kwargs):
        return _NoOpSpan()
