"""The FastAPI application factory, its correlation-ID middleware and problem responses.

Every response carries an ``X-Correlation-ID`` header: the caller's own value when it is a
safe token, otherwise a new one. Every error is an ``application/problem+json`` body
(``schemas.Problem``) carrying the same correlation ID:

- ``CodedError`` maps its stable code to a status (``_STATUS_BY_CODE``, else by failure
  class) and keeps its safe explanation as ``detail``;
- a malformed request is ``400 invalid_input``, an unknown path ``404 not_found``;
- anything else is ``500 internal_defect`` with a fixed detail; the exception is logged
  with the correlation ID and never reaches the response.

Process telemetry (``adapters.telemetry``) is configured at start-up. FastAPI's native
instrumentation then traces each request; the correlation ID is bound to the request's log
lines and recorded on its server span.
"""

import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from property_focus.adapters.auth.oidc import OidcVerifier, verifier_for
from property_focus.adapters.db import connect_runtime
from property_focus.adapters.rest.routes import health, v1
from property_focus.adapters.rest.schemas import Problem
from property_focus.adapters.telemetry import bind_correlation_id, configure_telemetry
from property_focus.config import Settings
from property_focus.domain.errors import CodedError, error_code

logger = logging.getLogger(__name__)

CORRELATION_HEADER = "X-Correlation-ID"
# Requests under this path are health probes.
_HEALTH_PATH_PREFIX = "/health/"
# An inbound correlation ID is kept only when it matches; anything else is replaced.
_SAFE_CORRELATION_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")

# HTTP status per stable code a request can end with (plan: REST error statuses).
_STATUS_BY_CODE: dict[str, int] = {
    "invalid_input": 422,
    "unsupported_source": 422,
    "unauthorized": 401,
    "forbidden": 403,
    "not_found": 404,
    "idempotency_conflict": 409,
    "material_change_requires_confirmation": 409,
    "owner_cannot_leave": 409,
    "rate_limited": 429,
    "budget_exceeded": 429,
    "dependency_transient": 503,
    "timeout": 503,
}


def create_app(
    settings: Settings,
    *,
    engine: Engine | None = None,
    verifier: OidcVerifier | None = None,
) -> FastAPI:
    """Build the API application for ``settings``.

    ``engine`` (the runtime role's pool) and ``verifier`` default to the ones ``settings``
    describe; the default engine is created at start-up and disposed at shutdown. Start-up
    also configures process telemetry, unless an earlier application or entry point in the
    process already did.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_telemetry(settings, "property-focus-api")
        if engine is not None:  # injected, and set on app.state below
            yield
            return
        app.state.engine = await connect_runtime(settings)
        try:
            yield
        finally:
            app.state.engine.dispose()

    app = FastAPI(
        title="Property Focus API",
        version="0.1.0",
        lifespan=lifespan,
        # Spans go only through the redacting providers configure_telemetry installs: no
        # exporters from OTEL_* variables, and no exception messages as OpenTelemetry logs.
        telemetry={"auto_configure": False, "logs": False},
    )
    if engine is not None:
        app.state.engine = engine
    app.state.verifier = verifier or verifier_for(settings)
    # The API accepts only tokens issued for it (PF_OIDC_AUDIENCE), never MCP client tokens.
    app.state.audiences = settings.oidc_audience
    app.add_middleware(CorrelationIdMiddleware)
    app.add_exception_handler(CodedError, _coded_error)
    app.add_exception_handler(RequestValidationError, _malformed_request)
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(Exception, _unexpected_error)
    app.include_router(health)
    app.include_router(v1)
    _document_problem_responses(app)
    return app


def _document_problem_responses(app: FastAPI) -> None:
    """Make the OpenAPI document match what the error handlers send.

    FastAPI documents every response model as ``application/json`` and adds a ``422``
    validation error to routes with parameters. Error responses are problems, and a
    malformed request is the documented ``400`` problem, so error content moves to
    ``application/problem+json`` and the ``422`` entries and their schemas are dropped.
    """
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        document = generate()
        for path in document["paths"].values():
            for operation in path.values():
                responses = operation.get("responses", {})
                responses.pop("422", None)
                for status, response in responses.items():
                    content = response.get("content", {})
                    if int(status) >= 400 and "application/json" in content:
                        content["application/problem+json"] = content.pop("application/json")
        for unused in ("HTTPValidationError", "ValidationError"):
            document.get("components", {}).get("schemas", {}).pop(unused, None)
        return document

    app.openapi = openapi  # type: ignore[method-assign]


class CorrelationIdMiddleware:
    """Gives each HTTP request a correlation ID (``request.state.correlation_id``), binds it
    to the request's log lines and span, and echoes it in the ``X-Correlation-ID`` response
    header.

    Each answered request is logged once, with its method, path (never the query string),
    status and duration: at INFO, except a successful health probe, which is logged at DEBUG
    because orchestrators repeat it every few seconds. A request that ends in an unhandled
    error is logged by the error handler instead.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        inbound = Headers(scope=scope).get(CORRELATION_HEADER, "")
        correlation_id = inbound if _SAFE_CORRELATION_ID.fullmatch(inbound) else uuid.uuid4().hex
        scope.setdefault("state", {})["correlation_id"] = correlation_id
        status: int | None = None

        async def send_with_header(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = MutableHeaders(scope=message)
                if CORRELATION_HEADER not in headers:
                    headers.append(CORRELATION_HEADER, correlation_id)
            await send(message)

        started = time.perf_counter()
        with bind_correlation_id(correlation_id):
            await self._app(scope, receive, send_with_header)
            probe = scope["path"].startswith(_HEALTH_PATH_PREFIX) and (status or 500) < 500
            logger.log(
                logging.DEBUG if probe else logging.INFO,
                "%s %s answered %s",
                scope["method"],
                scope["path"],
                status,
                extra={
                    "http_method": scope["method"],
                    "http_path": scope["path"],
                    "http_status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )


def problem_response(
    request: Request,
    code: str,
    detail: str,
    *,
    status: int,
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    """A problem response for the registered ``code``, carrying the request's correlation ID."""
    entry = error_code(code)
    correlation_id = getattr(request.state, "correlation_id", None) or uuid.uuid4().hex
    problem = Problem(
        type=f"urn:property-focus:problem:{code}",
        title=HTTPStatus(status).phrase,
        status=status,
        code=code,
        failure_class=entry.failure_class,
        detail=detail,
        correlation_id=correlation_id,
        retryable=entry.retries_automatically,
        retry_after_seconds=retry_after_seconds,
    )
    headers = {CORRELATION_HEADER: correlation_id}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(retry_after_seconds)
    body = problem.model_dump(mode="json")
    if retry_after_seconds is None:
        del body["retry_after_seconds"]
    return JSONResponse(
        body,
        status_code=status,
        headers=headers,
        media_type="application/problem+json",
    )


async def _coded_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, CodedError)
    status = _STATUS_BY_CODE.get(error.code)
    if status is None:
        status = 503 if error.error.failure_class == "TRANSIENT" else 500
    retry_after = (
        max(1, round(error.retry_after.total_seconds())) if error.retry_after is not None else None
    )
    return problem_response(
        request, error.code, error.safe_explanation, status=status, retry_after_seconds=retry_after
    )


async def _malformed_request(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, RequestValidationError)
    fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
    return problem_response(
        request, "invalid_input", f"Invalid request fields: {', '.join(fields)}.", status=400
    )


async def _http_error(request: Request, error: Exception) -> JSONResponse:
    assert isinstance(error, HTTPException)
    if error.status_code == 404:
        return problem_response(request, "not_found", "Nothing exists at this path.", status=404)
    if error.status_code < 500:
        detail = HTTPStatus(error.status_code).phrase
        return problem_response(request, "invalid_input", detail, status=error.status_code)
    return await _unexpected_error(request, error)


async def _unexpected_error(request: Request, error: Exception) -> JSONResponse:
    response = problem_response(
        request, "internal_defect", "An unexpected error occurred.", status=500
    )
    correlation_id = response.headers[CORRELATION_HEADER]
    # Starlette calls this handler outside the correlation middleware, so bind it again.
    with bind_correlation_id(correlation_id):
        logger.error(
            "unhandled error in %s %s (correlation ID %s)",
            request.method,
            request.url.path,
            correlation_id,
            exc_info=error,
        )
    return response
