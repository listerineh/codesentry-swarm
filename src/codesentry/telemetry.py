"""OpenTelemetry setup. One trace follows a task across the bus, the models and the sandbox."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from codesentry.config import Settings

TRACER_NAME = "codesentry"
_propagator = TraceContextTextMapPropagator()


class MemoryExporter(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans: tuple[ReadableSpan, ...] | list[ReadableSpan]) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        return None


def build_provider(settings: Settings) -> tuple[TracerProvider, MemoryExporter | None]:
    attributes = {"service.name": "codesentry-swarm"}
    if settings.mode == "gcp":
        attributes["gcp.project_id"] = settings.project_id
    provider = TracerProvider(resource=Resource.create(attributes))
    if settings.mode == "gcp":
        provider.add_span_processor(BatchSpanProcessor(_otlp_exporter()))
        return provider, None
    memory = MemoryExporter()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    return provider, memory


def _otlp_exporter() -> SpanExporter:
    """Cloud Trace over OTLP. Requires the Telemetry API (telemetry.googleapis.com) enabled on the project."""
    import google.auth
    import google.auth.transport.grpc
    import google.auth.transport.requests
    import grpc
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    credentials, _ = google.auth.default()
    plugin = google.auth.transport.grpc.AuthMetadataPlugin(
        credentials, google.auth.transport.requests.Request()
    )
    channel = grpc.composite_channel_credentials(
        grpc.ssl_channel_credentials(), grpc.metadata_call_credentials(plugin)
    )
    return OTLPSpanExporter(endpoint="https://telemetry.googleapis.com:443", credentials=channel)


def current_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    _propagator.inject(carrier)
    return carrier.get("traceparent")


def context_from(traceparent: str | None) -> Context | None:
    return _propagator.extract({"traceparent": traceparent}) if traceparent else None


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
