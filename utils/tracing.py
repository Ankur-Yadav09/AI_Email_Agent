"""Tracing: for each email, where the time went, what the model was asked and answered, and what each tool did.

Built on OpenTelemetry, the vendor-neutral standard, using the OpenInference attribute names that LLM
observability tools understand. The same traces can go to a self-hosted Arize Phoenix (bundled in
docker-compose.yml), Langfuse, LangSmith or any OTLP backend; see the README.

Safe by default:
  * OFF unless an OTLP endpoint is configured. Off means "no-op": nothing is recorded or sent.
  * Metadata only (timings, token counts, intents, tool names, sizes). The text of prompts and answers is
    recorded only when TRACE_CONTENT=true, and even then account/card numbers are masked.
  * Tracing can never break the service: set-up failures are logged and ignored, and the exporter runs in the
    background so a slow or unreachable backend does not slow the pipeline.
"""
import json
import logging
import os
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import NoOpTracer

from utils.logging_config import mask_text, mask_value

logger = logging.getLogger(__name__)

_tracer = NoOpTracer()
_provider = None


def enabled() -> bool:
    return _provider is not None


def content_enabled() -> bool:
    """Record the text of prompts and answers? Off by default: it is customer content."""
    return os.environ.get("TRACE_CONTENT", "").strip().lower() == "true"


def configure(exporter=None, batch: bool = True) -> None:
    """Start recording. By default spans go to OTLP/HTTP configured by the standard OTEL_EXPORTER_OTLP_* variables."""
    global _tracer, _provider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # A short timeout (standard OTEL_EXPORTER_OTLP_TIMEOUT overrides it) so a dead backend cannot hold up shutdown.
        exporter = OTLPSpanExporter(timeout=float(os.environ.get("OTEL_EXPORTER_OTLP_TIMEOUT") or 3))

    provider = TracerProvider(resource=Resource.create({
        "service.name": os.environ.get("OTEL_SERVICE_NAME") or "ai-email-agent",
        "openinference.project.name": os.environ.get("TRACE_PROJECT") or "ai-email-agent",  # Phoenix project
    }))
    provider.add_span_processor((BatchSpanProcessor if batch else SimpleSpanProcessor)(exporter))
    _provider, _tracer = provider, provider.get_tracer("ai_email_agent")


def setup_tracing() -> bool:
    """Called once at start-up. Turns tracing on only if an OTLP endpoint is configured. Never raises."""
    if not (os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT") or os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")):
        return False
    try:
        configure()
    except Exception:  # noqa: BLE001
        logger.exception("Tracing could not start; continuing without it")
        return False
    logger.info("Tracing is on (content capture: %s)", "ON, masked" if content_enabled() else "off")
    return True


def shutdown() -> None:
    """Send whatever is still queued. Called when the service stops."""
    global _tracer, _provider
    if _provider is not None:
        try:
            _provider.shutdown()
        except Exception:  # noqa: BLE001
            logger.exception("Tracing shutdown failed")
        _provider, _tracer = None, NoOpTracer()


def warn_if_cloud_tracing() -> None:
    """LangGraph traces to LangSmith (a cloud service) by itself if these variables are set, raw customer data included."""
    flag = os.environ.get("LANGSMITH_TRACING") or os.environ.get("LANGCHAIN_TRACING_V2") or ""
    if flag.strip().lower() == "true":
        logger.warning(
            "LangSmith tracing is ON: customer emails, account numbers and tool results in the workflow state "
            "are sent, unmasked, to the LangSmith service. Turn it off (LANGSMITH_TRACING) unless that is approved."
        )


def _clean(value):
    """Attribute values: numbers and booleans as they are; text masked; anything else as masked JSON."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return mask_text(value)
    return mask_text(json.dumps(mask_value(value), default=str))


def set_attributes(span, attributes: dict) -> None:
    for key, value in attributes.items():
        value = _clean(value)
        if value is not None:
            span.set_attribute(key, value)


def annotate(**attributes) -> None:
    """Add attributes to the span that is currently open (keys use '__' for '.': email__id becomes email.id)."""
    set_attributes(trace.get_current_span(), {k.replace("__", "."): v for k, v in attributes.items()})


@contextmanager
def span(name: str, kind: str = "CHAIN", **attributes):
    """Open a span. `kind` is an OpenInference kind (CHAIN, LLM, TOOL, ...). Exceptions mark the span as an error."""
    with _tracer.start_as_current_span(name) as current:
        current.set_attribute("openinference.span.kind", kind)
        set_attributes(current, {k.replace("__", "."): v for k, v in attributes.items()})
        yield current


def current_trace_id() -> str | None:
    """Trace id of the open span, so an audit-log line can be matched with its trace."""
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None
