"""Telemetry: OpenTelemetry traces and metrics, structured JSON logs and redaction (D50, D49).

``configure_telemetry`` installs, once per process, a tracer and a meter provider that export
over OTLP/HTTP to ``settings.otlp_endpoint`` (nothing is exported without one), and a root
log handler that writes one JSON object per line to standard error. Each log line carries
the current ``trace_id`` and ``span_id`` and the ``correlation_id`` that
``bind_correlation_id`` set for the request or task.

Export never blocks the caller: spans and metrics leave in background batches, and a batch
the collector does not accept is dropped after a short timeout.

Redaction applies to every log line and to every span before it is exported:

- every value a ``SecretProvider`` returned in this process (``register_secret``);
- values under sensitive keys: credentials, tokens, cookies, sessions, email bodies and
  financial details (``redact_attribute``);
- bearer and basic credentials, JSON Web Tokens, cookie headers, URL user-information
  passwords, and ``key=value`` pairs whose key is sensitive by the same rule, inside free
  text, URLs included (``redact_text``).

Work that crosses an asynchronous hop (an outbox event, a workflow run) records the W3C
trace context it was created in (``current_trace_context``); the code that picks the work
up later continues that trace with ``resume_trace``, whose span is a child of the recorded
context and also links to it.
"""

import json
import logging
import re
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from importlib.metadata import version
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
from opentelemetry.trace import Link, Span, Status
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from property_focus.config import Settings

REDACTED = "[REDACTED]"

# Seconds one OTLP export may take, retries included, before its batch is dropped. Bounds
# how long a process waits at shutdown when the collector is down.
_EXPORT_TIMEOUT_SECONDS = 3.0
# Registered secret values shorter than this are not scrubbed from free text: they would
# match inside ordinary words and identifiers throughout every log line.
_MIN_SECRET_LENGTH = 6

# Key words (after splitting a key on punctuation and case) whose values are always redacted.
_SENSITIVE_KEY_WORDS = frozenset(
    {
        # credentials and sessions
        "apikey",
        "authorization",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "jwt",
        "passphrase",
        "passwd",
        "password",
        "secret",
        "session",
        "signature",
        "token",
        # email content
        "body",
        # financial details
        "financial",
        "income",
        "salary",
        "ssn",
        "iban",
    }
)
# Multi-word key fragments (in normalized snake case) whose values are always redacted.
_SENSITIVE_KEY_PHRASES = (
    "api_key",
    "account_number",
    "routing_number",
    "card_number",
    "credit_card",
    "credit_score",
    "bank_account",
    "net_worth",
    "down_payment",
    "tax_id",
    "walk_away",
    "hard_limit",
    "code_verifier",
)
# Every pattern below runs in time linear in the text, because redaction runs on the request
# path over client-controlled values (paths, query strings, request headers). A pattern may
# start only at the start of a run of the characters it scans (the negative lookbehinds), and
# possessive quantifiers (``++``, ``*+``) never give back what they matched, so no character
# is rescanned by a later candidate.
_TEXT_PATTERNS = (
    # Authorization header values.
    (re.compile(r"(?i)\b(bearer|basic)\s++[A-Za-z0-9._~+/=-]++"), rf"\1 {REDACTED}"),
    # JSON Web Tokens anywhere.
    (
        re.compile(r"(?<![\w-])eyJ[A-Za-z0-9_-]++\.[A-Za-z0-9_-]++\.[A-Za-z0-9_-]*+"),
        REDACTED,
    ),
    # Cookie and Set-Cookie headers, to the end of the line.
    (re.compile(r"(?im)\b((?:set-)?cookie\s*+[:=]\s*+)[^\r\n]+"), rf"\1{REDACTED}"),
    # The password in a URL's user information (scheme://user:password@host).
    (
        re.compile(r"(?i)(?<![a-z0-9+.-])([a-z][a-z0-9+.-]*+://[^\s:/?#@]*+:)[^\s/?#@]++(?=@)"),
        rf"\1{REDACTED}",
    ),
)
# The key and separator of a key=value, key: value or "key": "value" pair. A key is a whole
# run of key characters, such as ``--password`` or ``x-api-key``; only the key and separator
# are consumed, so a non-sensitive pair never hides the pairs inside its value, such as the
# query parameters of a URL or a nested ``prefix:key=value``.
_KEY_SEPARATOR = re.compile(
    r"""(?<![A-Za-z0-9_.-])(?P<key>[A-Za-z0-9_.-]++)["']?\s*+[:=]\s*+["']?"""
)
# The value of a pair, matched only where a sensitive key's separator ends.
_VALUE = re.compile(r"""[^\s"'&,;]++""")
_KEY_WORD_BOUNDARY = re.compile(r"[^a-z0-9]+")
_CAMEL_CASE_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
# Up to this many adjacent key words are also joined and looked up, so a sensitive word that
# camelCase split apart (``passWord``, ``apiKey``) is still found.
_MAX_JOINED_KEY_WORDS = 3
# Matches every key ``is_sensitive_key`` accepts (and more): one sensitive word or phrase,
# in any case, with any punctuation between its letters. Keys it does not match are
# rejected by one regular-expression search, which keeps a text of many short pairs cheap.
_SENSITIVE_KEY_CANDIDATE = re.compile(
    "(?i)"
    + "|".join(
        "[^a-z0-9]*".join(term.replace("_", ""))
        for term in sorted(_SENSITIVE_KEY_WORDS) + list(_SENSITIVE_KEY_PHRASES)
    )
)

_secrets_lock = threading.Lock()
_secret_values: set[str] = set()
_secret_pattern: re.Pattern[str] | None = None


def register_secret(value: str) -> None:
    """Redact ``value`` from every later log line and exported span in this process.

    Secret providers call this for each value they return. Values shorter than six
    characters are redacted only under sensitive keys, not inside free text.
    """
    global _secret_pattern
    if len(value) < _MIN_SECRET_LENGTH:
        return
    with _secrets_lock:
        if value in _secret_values:
            return
        _secret_values.add(value)
        # Longest first, so a secret that contains another is removed whole.
        ordered = sorted(_secret_values, key=len, reverse=True)
        _secret_pattern = re.compile("|".join(re.escape(secret) for secret in ordered))


def redact_text(text: str) -> str:
    """``text`` with registered secrets and recognizable credentials replaced by
    ``[REDACTED]``."""
    pattern = _secret_pattern
    if pattern is not None:
        text = pattern.sub(REDACTED, text)
    for credential, replacement in _TEXT_PATTERNS:
        text = credential.sub(replacement, text)
    return _redact_sensitive_values(text)


def _redact_sensitive_values(text: str) -> str:
    """``text`` with the value of every key=value-style pair whose key ``is_sensitive_key``
    accepts replaced by ``[REDACTED]``; every other pair is left as it was."""
    parts: list[str] = []
    copied_up_to = 0
    match = _KEY_SEPARATOR.search(text)
    while match is not None:
        value = _VALUE.match(text, match.end()) if is_sensitive_key(match["key"]) else None
        if value is None:
            match = _KEY_SEPARATOR.search(text, match.end())
            continue
        parts += (text[copied_up_to : value.start()], REDACTED)
        copied_up_to = value.end()
        # A key inside the redacted value goes with it.
        match = _KEY_SEPARATOR.search(text, copied_up_to)
    parts.append(text[copied_up_to:])
    return "".join(parts)


def is_sensitive_key(key: str) -> bool:
    """True when values stored under ``key`` (a log field or span attribute name) are never
    recorded, whatever they contain."""
    if _SENSITIVE_KEY_CANDIDATE.search(key) is None:
        return False
    normalized = _KEY_WORD_BOUNDARY.sub("_", _CAMEL_CASE_BOUNDARY.sub("_", key).lower())
    words = normalized.strip("_").split("_")
    joined_words = (
        "".join(words[start : start + count])
        for count in range(1, _MAX_JOINED_KEY_WORDS + 1)
        for start in range(len(words) - count + 1)
    )
    return any(word in _SENSITIVE_KEY_WORDS for word in joined_words) or any(
        phrase in normalized for phrase in _SENSITIVE_KEY_PHRASES
    )


def redact_attribute(key: str, value: Any) -> Any:
    """``value`` as it may be recorded under ``key``.

    Values under a sensitive key become ``[REDACTED]``; strings are scrubbed with
    ``redact_text``; mappings and sequences are redacted item by item; numbers, booleans
    and None pass through.
    """
    if is_sensitive_key(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {str(k): redact_attribute(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(redact_attribute(key, item) for item in value)
    return value


def _redacted_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    return {key: redact_attribute(key, value) for key, value in (attributes or {}).items()}


# Correlation and trace context -------------------------------------------------------

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_PROPAGATOR = TraceContextTextMapPropagator()
_tracer = trace.get_tracer("property_focus")


@contextmanager
def bind_correlation_id(correlation_id: str | None) -> Iterator[None]:
    """Tag the log lines written inside the block, and the current span, with
    ``correlation_id``."""
    token = _correlation_id.set(correlation_id)
    if correlation_id:
        trace.get_current_span().set_attribute("property_focus.correlation_id", correlation_id)
    try:
        yield
    finally:
        _correlation_id.reset(token)


def current_trace_context() -> dict[str, str]:
    """The current span's W3C trace context (``traceparent``, and ``tracestate`` when set),
    for storing with work picked up later; empty outside any span. Baggage is never
    included."""
    carrier: dict[str, str] = {}
    _PROPAGATOR.inject(carrier)
    return carrier


@contextmanager
def resume_trace(
    name: str, trace_context: Mapping[str, Any], *, attributes: Mapping[str, Any] | None = None
) -> Iterator[Span]:
    """Run the block in a new current span that continues work recorded with
    ``trace_context`` (from ``current_trace_context``).

    The span joins the recorded trace as a child of the recorded span and links to it, so the
    asynchronous hop stays visible even when the recorded span ended long before. Without a
    valid recorded context the span continues the current trace and has no link.
    """
    carrier = {key: value for key, value in trace_context.items() if isinstance(value, str)}
    recorded = _PROPAGATOR.extract(carrier)
    recorded_span = trace.get_current_span(recorded).get_span_context()
    if recorded_span.is_valid:
        parent, links = recorded, [Link(recorded_span)]
    else:
        parent, links = otel_context.get_current(), []
    with _tracer.start_as_current_span(
        name, context=parent, links=links, attributes=dict(attributes or {})
    ) as span:
        yield span


# Logs --------------------------------------------------------------------------------

# Attributes every LogRecord has; anything else on a record came from ``extra``.
_STANDARD_RECORD_FIELDS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    "taskName",
}


class JsonLogFormatter(logging.Formatter):
    """Formats a record as one redacted JSON object.

    Fields: ``timestamp`` (UTC, ISO 8601), ``level``, ``logger``, ``message``, ``trace_id``
    and ``span_id`` of the current span (null outside one), ``correlation_id`` (null when
    none is bound), ``exception`` and ``stack`` when present, and every ``extra`` field.
    """

    def format(self, record: logging.LogRecord) -> str:
        span = trace.get_current_span().get_span_context()
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "trace_id": trace.format_trace_id(span.trace_id) if span.is_valid else None,
            "span_id": trace.format_span_id(span.span_id) if span.is_valid else None,
            "correlation_id": _correlation_id.get(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_RECORD_FIELDS and key not in entry:
                entry[key] = redact_attribute(key, value)
        if record.exc_info:
            entry["exception"] = redact_text(self.formatException(record.exc_info))
        if record.stack_info:
            entry["stack"] = redact_text(self.formatStack(record.stack_info))
        return json.dumps(entry, default=lambda value: redact_text(str(value)))


# Spans -------------------------------------------------------------------------------


class _RedactingSpanProcessor(SpanProcessor):
    """Hands ``processor`` a redacted copy of each finished span."""

    def __init__(self, processor: SpanProcessor) -> None:
        self._processor = processor

    def on_end(self, span: ReadableSpan) -> None:
        status = span.status
        if status.description:
            status = Status(status.status_code, redact_text(status.description))
        self._processor.on_end(
            ReadableSpan(
                name=span.name,
                context=span.context,
                parent=span.parent,
                resource=span.resource,
                attributes=_redacted_attributes(span.attributes),
                events=[
                    Event(event.name, _redacted_attributes(event.attributes), event.timestamp)
                    for event in span.events
                ],
                links=span.links,
                kind=span.kind,
                status=status,
                start_time=span.start_time,
                end_time=span.end_time,
                instrumentation_scope=span.instrumentation_scope,
            )
        )

    def shutdown(self) -> None:
        self._processor.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._processor.force_flush(timeout_millis)


# Process set-up ----------------------------------------------------------------------

_setup_lock = threading.Lock()
_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None


def configure_telemetry(settings: Settings, service_name: str) -> None:
    """Install tracing, metrics and JSON logging for this process, as ``service_name``.

    Only the first call in a process takes effect; later calls do nothing, so every
    application factory and entry point may call it. Spans and metrics are exported to
    ``settings.otlp_endpoint`` when it is set.
    """
    global _tracer_provider, _meter_provider
    with _setup_lock:
        if _tracer_provider is not None:
            return
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.version": version("property-focus"),
                "deployment.environment.name": settings.environment,
            }
        )
        tracer_provider = TracerProvider(resource=resource)
        readers: list[MetricReader] = []
        if settings.otlp_endpoint is not None:
            base = settings.otlp_endpoint.rstrip("/")
            tracer_provider.add_span_processor(
                _RedactingSpanProcessor(
                    BatchSpanProcessor(
                        OTLPSpanExporter(
                            endpoint=f"{base}/v1/traces", timeout=_EXPORT_TIMEOUT_SECONDS
                        )
                    )
                )
            )
            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(
                        endpoint=f"{base}/v1/metrics", timeout=_EXPORT_TIMEOUT_SECONDS
                    )
                )
            )
        meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        trace.set_tracer_provider(tracer_provider)
        metrics.set_meter_provider(meter_provider)
        _install_json_logging()
        _tracer_provider, _meter_provider = tracer_provider, meter_provider


def _install_json_logging() -> None:
    """Send root-logger records at INFO and above to standard error as JSON lines."""
    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def add_span_exporter(exporter: SpanExporter) -> None:
    """Also export this process's finished spans to ``exporter``, redacted and in background
    batches (``flush_telemetry`` waits for them). Requires ``configure_telemetry`` first."""
    if _tracer_provider is None:
        raise RuntimeError("configure_telemetry must run before add_span_exporter")
    _tracer_provider.add_span_processor(_RedactingSpanProcessor(BatchSpanProcessor(exporter)))


def flush_telemetry() -> None:
    """Wait until the spans and metrics recorded so far have been exported or dropped.

    Short-lived processes call it before exiting; the providers also flush at interpreter
    exit.
    """
    for provider in (_tracer_provider, _meter_provider):
        if provider is not None:
            provider.force_flush()
