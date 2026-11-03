"""Telemetry against the real application: JSON logs, redaction and linked spans.

The process's telemetry is configured once (as the application would at start-up) with an
in-memory span exporter added behind the same redaction as the OTLP exporter, and a JSON
log handler is attached to the root logger for each test.
"""

import asyncio
import io
import json
import logging
import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from sqlalchemy import Engine

from property_focus.adapters.auth.oidc import verifier_for
from property_focus.adapters.db.models import WorkflowRun
from property_focus.adapters.db.repositories import ServiceScope, TenantScope, UnitOfWork
from property_focus.adapters.rest.app import CORRELATION_HEADER, create_app
from property_focus.adapters.secrets import DeploymentInjectedSecretProvider
from property_focus.adapters.telemetry import (
    JsonLogFormatter,
    add_span_exporter,
    configure_telemetry,
    flush_telemetry,
)
from property_focus.application.ports import TaskInput
from property_focus.application.workflows import NewEvent, admit_run, task_input_for, utc_now
from property_focus.config import Settings
from property_focus.runtime.run_task import TaskContext, TaskResult, TaskRuntime, run_task

logger = logging.getLogger("property_focus.tests.telemetry")

SUBJECT = uuid.UUID("7d0c5b7e-2f4e-4a8b-9c1d-3e5f7a9b1c2d")


@pytest.fixture(scope="session")
def span_exporter(settings: Settings) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    configure_telemetry(settings, "property-focus-test")
    add_span_exporter(exporter)
    return exporter


@pytest.fixture
def exported_spans(span_exporter: InMemorySpanExporter) -> Callable[[], Sequence[ReadableSpan]]:
    """Return the spans exported since the test started, after a flush."""
    flush_telemetry()
    span_exporter.clear()

    def finished() -> Sequence[ReadableSpan]:
        flush_telemetry()
        return span_exporter.get_finished_spans()

    return finished


@pytest.fixture
def log_output() -> Iterator[io.StringIO]:
    """Everything the root logger writes during the test, formatted as the process logs it."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)


def log_lines(stream: io.StringIO) -> list[dict[str, Any]]:
    """Each line written to ``stream`` parsed as JSON; fails on a line that is not."""
    return [json.loads(line) for line in stream.getvalue().splitlines()]


class RecordingLauncher:
    """Keeps launched envelopes, for the test to run as an execution service would."""

    def __init__(self) -> None:
        self.launched: list[TaskInput] = []

    async def launch(self, task: TaskInput) -> str:
        self.launched.append(task)
        return f"recorded:{len(self.launched)}"

    def take(self) -> TaskInput:
        assert len(self.launched) == 1, self.launched
        return self.launched.pop()


async def announce_subject(context: TaskContext) -> TaskResult:
    logger.info("announcing subject %s", context.task.subject_id)
    context.emit(
        NewEvent(
            event_type="subject.announced",
            aggregate_type=context.task.subject_type,
            aggregate_id=context.task.subject_id,
            aggregate_revision=1,
            destinations=("note-subject",),
        )
    )
    return TaskResult()


async def note_subject(context: TaskContext) -> TaskResult:
    logger.info("noting subject %s", context.task.subject_id)
    return TaskResult()


def launching_api(
    settings: Settings, engine: Engine, tenant: TenantScope, launcher: RecordingLauncher
) -> TestClient:
    """The REST application plus one route that admits an ``announce-subject`` run for
    ``tenant`` and launches it after commit, as a launching endpoint does."""
    app = create_app(settings, engine=engine, verifier=verifier_for(settings))

    @app.post("/test/announcements")
    async def announce(request: Request) -> dict[str, str]:
        logger.info("admitting an announcement")
        with UnitOfWork(engine, tenant) as uow:
            admission = admit_run(
                uow,
                operation="test.announce",
                idempotency_key=uuid.uuid4().hex,
                kind="announce-subject",
                subject_type="property",
                subject_id=SUBJECT,
                now=utc_now(),
                correlation_id=request.state.correlation_id,
            )
        with UnitOfWork(engine, ServiceScope()) as uow:
            run = uow.session.get_one(WorkflowRun, admission.workflow_run_id)
            await launcher.launch(task_input_for(run))
        return {"workflow_run_id": str(admission.workflow_run_id)}

    return TestClient(app, raise_server_exceptions=False)


def only(spans: Sequence[ReadableSpan], name: str) -> ReadableSpan:
    matching = [span for span in spans if span.name == name]
    assert len(matching) == 1, [span.name for span in spans]
    return matching[0]


def assert_resumes(span: ReadableSpan, recorded: ReadableSpan) -> None:
    """``span`` continues ``recorded``'s trace as its child and links to it."""
    assert span.context is not None
    assert recorded.context is not None
    assert span.context.trace_id == recorded.context.trace_id
    assert span.parent is not None
    assert span.parent.span_id == recorded.context.span_id
    assert [link.context.span_id for link in span.links] == [recorded.context.span_id]


def test_a_rest_request_that_launches_a_task_produces_linked_spans(
    settings: Settings,
    runtime_engine: Engine,
    new_tenant: Callable[[], TenantScope],
    exported_spans: Callable[[], Sequence[ReadableSpan]],
    log_output: io.StringIO,
) -> None:
    launcher = RecordingLauncher()
    runtime = TaskRuntime(
        engine=runtime_engine,
        launcher=launcher,
        secrets=DeploymentInjectedSecretProvider({}),
        tasks={"announce-subject": announce_subject, "note-subject": note_subject},
    )

    with launching_api(settings, runtime_engine, new_tenant(), launcher) as client:
        response = client.post(
            "/test/announcements", headers={CORRELATION_HEADER: "req-telemetry-1"}
        )
    assert response.status_code == 200
    assert asyncio.run(run_task(launcher.take(), runtime)) == "complete"
    assert asyncio.run(run_task(launcher.take(), runtime)) == "complete"

    spans = exported_spans()
    request = only(spans, "POST /test/announcements")
    assert request.kind == SpanKind.SERVER
    assert request.attributes is not None
    assert request.attributes["property_focus.correlation_id"] == "req-telemetry-1"
    assert request.context is not None
    request_trace = request.context.trace_id

    # Admission recorded the request's context on the run; the task resumes it.
    upstream = only(spans, "task announce-subject")
    assert upstream.parent is not None
    recorded_by_request = next(
        span
        for span in spans
        if span.context is not None and span.context.span_id == upstream.parent.span_id
    )
    assert recorded_by_request.context is not None
    assert recorded_by_request.context.trace_id == request_trace
    assert_resumes(upstream, recorded_by_request)
    assert upstream.attributes is not None
    assert upstream.attributes["property_focus.task_outcome"] == "complete"
    assert upstream.attributes["property_focus.correlation_id"] == "req-telemetry-1"

    # The task's event carries its context through the outbox to the downstream run.
    assert_resumes(only(spans, "publish note-subject"), upstream)
    downstream = only(spans, "task note-subject")
    assert_resumes(downstream, upstream)

    lines = {line["message"]: line for line in log_lines(log_output)}
    trace_id = trace.format_trace_id(request_trace)
    for message in (
        "admitting an announcement",
        "POST /test/announcements answered 200",
        f"announcing subject {SUBJECT}",
        f"noting subject {SUBJECT}",
    ):
        assert lines[message]["trace_id"] == trace_id
        assert lines[message]["correlation_id"] == "req-telemetry-1"
        assert lines[message]["span_id"]
    assert lines[f"noting subject {SUBJECT}"]["span_id"] == trace.format_span_id(
        downstream.context.span_id  # type: ignore[union-attr]
    )


def test_logs_are_json_lines_with_trace_and_correlation_ids(
    api: TestClient, span_exporter: InMemorySpanExporter, log_output: io.StringIO
) -> None:
    with trace.get_tracer(__name__).start_as_current_span("job") as span:
        logger.info("one structured line", extra={"listing_count": 3})
    logger.info("outside any span")

    inside, outside = log_lines(log_output)
    assert inside["message"] == "one structured line"
    assert inside["level"] == "INFO"
    assert inside["logger"] == logger.name
    assert inside["listing_count"] == 3
    assert inside["trace_id"] == trace.format_trace_id(span.get_span_context().trace_id)
    assert inside["span_id"] == trace.format_span_id(span.get_span_context().span_id)
    assert outside["trace_id"] is None
    assert outside["correlation_id"] is None
    assert inside["timestamp"].endswith("+00:00")


def test_seeded_secret_values_never_appear_in_logs_or_spans(
    api: TestClient,
    exported_spans: Callable[[], Sequence[ReadableSpan]],
    log_output: io.StringIO,
) -> None:
    api_key = f"sk-seeded-{uuid.uuid4().hex}"
    database_password = f"pw-{uuid.uuid4().hex[:12]}"
    secrets = DeploymentInjectedSecretProvider(
        {"PF_SEEDED_API_KEY": api_key, "PF_SEEDED_DATABASE_PASSWORD": database_password}
    )
    asyncio.run(secrets.get("seeded_api_key"))
    asyncio.run(secrets.get("seeded_database_password"))
    # A JWT-shaped synthetic token, assembled so the repository credential scan has no
    # literal to flag.
    access_token = ".".join(("eyJhbGciOiJSUzI1NiJ9", "eyJzdWIiOiJzZWVkZWQifQ", uuid.uuid4().hex))
    session_cookie = f"sid-{uuid.uuid4().hex}"
    email_body = "Dear buyer, the seller accepts 812345 for 12 Seeded Lane."
    income = 187654
    never_recorded = [api_key, database_password, access_token, session_cookie, email_body]

    logger.info("connecting with password %s", database_password)
    logger.info(
        "provider call",
        extra={
            "note": f"used key {api_key}",
            "email_body": email_body,
            "household_income": income,
            "headers": {"Authorization": f"Bearer {access_token}", "Accept": "text/html"},
        },
    )
    logger.info("request headers: Cookie: session=%s; theme=dark", session_cookie)
    try:
        raise RuntimeError(f"provider rejected {api_key}")
    except RuntimeError:
        logger.exception("provider call failed")

    tracer = trace.get_tracer(__name__)
    with tracer.start_as_current_span("provider call") as span:
        span.set_attribute("provider.request", f"key={api_key}&q=homes")
        span.set_attribute("http.request.header.cookie", f"session={session_cookie}")
        span.set_attribute("buyer.down_payment", income)
        span.record_exception(ValueError(f"rejected password {database_password}"))
    api.get(
        f"/health/live?access_token={access_token}&page=2",
        headers={"Authorization": f"Bearer {access_token}", "Cookie": f"session={session_cookie}"},
    )

    logs = log_output.getvalue()
    spans = "\n".join(span.to_json() for span in exported_spans())
    assert "/health/live" in spans
    assert "provider rejected" in logs
    for value in never_recorded:
        assert value not in logs
        assert value not in spans
    assert str(income) not in logs
    assert str(income) not in spans
    lines = log_lines(log_output)
    assert lines[1]["headers"]["Accept"] == "text/html"
    assert all(line["message"] for line in lines)


def test_requests_do_not_wait_for_an_unreachable_collector(settings: Settings) -> None:
    script = """
import json, time
from fastapi.testclient import TestClient
from property_focus.adapters.rest.app import create_app
from property_focus.config import Settings

settings = Settings.model_validate({"environment": "development", "otlp_endpoint": "http://127.0.0.1:9"})
with TestClient(create_app(settings)) as client:
    started = time.monotonic()
    statuses = {client.get("/health/live").status_code for _ in range(50)}
    elapsed = time.monotonic() - started
print(json.dumps({"statuses": sorted(statuses), "elapsed_seconds": elapsed}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PF_ENVIRONMENT": "development"},
        check=True,
    )
    result = json.loads(completed.stdout.splitlines()[-1])
    assert result["statuses"] == [200]
    assert result["elapsed_seconds"] < 5
