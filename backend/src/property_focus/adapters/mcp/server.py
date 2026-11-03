"""The MCP server: the official MCP Python SDK serving our tools at ``/mcp``.

The SDK's low-level ``Server`` owns the protocol: JSON-RPC, both protocol eras (the
initialize-based revisions and 2026-07-28, chosen per request), version negotiation, the
2026-07-28 header checks and error codes, and the streamable HTTP transport. It runs
stateless with JSON responses: no ``Mcp-Session-Id``, one ``application/json`` response per
request, ``202`` for a notification, ``405`` for ``GET`` and ``DELETE``, so any replica (or
one function invocation) can answer any request.

This module binds the SDK to the application:

- Tools. ``create_app`` takes the ``Tool`` registry to serve. Each tool runs its use case
  in-process in a worker thread, never through the REST API; a ``CodedError`` it raises
  becomes a tool error result with the body ``{"code", "detail"}``, and any other exception
  is logged and answered with a generic JSON-RPC internal error.
- Sign-in. Every request needs a bearer access token, checked by the same
  ``oidc.authenticate`` as REST, which also resolves the caller's tenant
  (``SharedTokenVerifier``). A request without a valid token gets the SDK's ``401``
  challenge naming the protected-resource metadata, which names ``PF_OIDC_ISSUER`` and is
  served at ``/.well-known/oauth-protected-resource/mcp`` and at the root form
  ``/.well-known/oauth-protected-resource``. When the identity provider or the database is
  unavailable the answer is ``503``, never ``401``. Nothing here reads which client is
  calling.
- Transport security. Only the ``Host`` and ``Origin`` values ``PF_MCP_ALLOWED_HOSTS`` and
  ``PF_MCP_ALLOWED_ORIGINS`` name are served; the check runs after sign-in.

Process telemetry (``adapters.telemetry``) is configured at start-up, with the SDK
transport's per-request INFO lines turned off. Each request is traced in one server span,
with its correlation ID bound to its log lines and recorded on that span; the SDK's own
per-message span is turned off so a request is not traced twice.
"""

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

import jwt
import mcp_types as types
from mcp.server import CacheHint, Server, ServerRequestContext
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.routes import create_protected_resource_routes
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from pydantic import AnyHttpUrl, BaseModel, ValidationError
from sqlalchemy import Engine
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.opentelemetry import OpenTelemetryMiddleware
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from property_focus.adapters.auth.oidc import OidcVerifier, authenticate, verifier_for
from property_focus.adapters.db import connect_runtime
from property_focus.adapters.telemetry import bind_correlation_id, configure_telemetry
from property_focus.application.context import ExecutionContext
from property_focus.config import McpEndpoint, Settings
from property_focus.domain.errors import CodedError

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"
# The protected-resource metadata's root form, kept beside the SDK's path-inserted form
# for clients that look there.
ROOT_RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"
# How long a 2026-07-28 client may reuse a discovery or tool-list result for the same user.
_CACHE_HINT = CacheHint(ttl_ms=300_000, scope="private")
# The SDK requires a client ID on every access token. This constant fills it; no code reads
# it, because MCP access does not depend on which client calls.
_CLIENT_ID = "mcp-client"
# JSON-RPC's code for an error inside the server: sent with HTTP 503 when the token check
# finds a dependency unavailable, and in a 200 response when a tool fails unexpectedly.
_INTERNAL_ERROR = -32603
# The only message an unexpected tool failure sends, the one the SDK itself uses in 2026-07-28.
_INTERNAL_ERROR_MESSAGE = "Internal server error"

# The SDK's streamable HTTP transport logger.
_SDK_TRANSPORT_LOGGER = "mcp.server.streamable_http"

# The correlation ID of the request being served, for the token check's tenant resolution.
_request_correlation_id: ContextVar[str | None] = ContextVar(
    "mcp_request_correlation_id", default=None
)


@dataclass(frozen=True)
class Tool[Arguments: BaseModel]:
    """An MCP tool bound to one application use case.

    ``run`` executes the use case for the authenticated caller in a worker thread, with
    the call's arguments validated as ``arguments``, and returns an instance of ``result``,
    whose JSON schema is the tool's output schema. A ``CodedError`` it raises becomes a tool
    error result carrying the error's code and safe explanation.
    """

    name: str
    description: str
    arguments: type[Arguments]
    result: type[BaseModel]
    run: Callable[[ExecutionContext, Engine, Arguments], BaseModel]

    def descriptor(self) -> types.Tool:
        """The tool as ``tools/list`` describes it."""
        return types.Tool(
            name=self.name,
            description=self.description,
            input_schema=self.arguments.model_json_schema(),
            output_schema=self.result.model_json_schema(),
        )


class CallerAccessToken(AccessToken):
    """An access token that passed the shared check, with the caller it identifies.

    ``client_id`` is a constant that nothing reads (MCP access is client-agnostic).
    """

    context: ExecutionContext


class IdentityUnavailableError(Exception):
    """The token could not be checked because the identity provider or the database is
    unavailable; the request gets ``503``. The message is safe to send to the client."""


class SharedTokenVerifier:
    """The SDK's ``TokenVerifier`` over ``oidc.authenticate``, the check REST uses too.

    ``engine`` returns the runtime role's pool, in which a caller's first request
    provisions their user and tenant.
    """

    def __init__(
        self, verifier: OidcVerifier, engine: Callable[[], Engine], *, clock_skew_seconds: int
    ) -> None:
        self._verifier = verifier
        self._engine = engine
        self._clock_skew_seconds = clock_skew_seconds

    async def verify_token(self, token: str) -> CallerAccessToken | None:
        """The caller ``token`` identifies, or None when the token is invalid, so the SDK
        answers with its ``401`` challenge.

        Raises ``IdentityUnavailableError`` when the issuer's keys or the database cannot be
        reached.
        """
        correlation_id = _request_correlation_id.get() or uuid.uuid4().hex
        try:
            context = await authenticate(
                self._verifier, self._engine(), token, correlation_id=correlation_id
            )
        except CodedError as error:
            if error.code == "unauthorized":
                return None
            if error.code == "dependency_transient":
                raise IdentityUnavailableError(error.safe_explanation) from error
            raise
        # authenticate has just verified this token, so its claims can be read as they are.
        claims = jwt.decode(token, options={"verify_signature": False})
        scope = claims.get("scope")
        return CallerAccessToken(
            token=token,
            client_id=_CLIENT_ID,
            scopes=scope.split() if isinstance(scope, str) else [],
            # The SDK checks expiry again without leeway; allow the skew the shared check does.
            expires_at=int(claims["exp"]) + self._clock_skew_seconds,
            context=context,
        )


def create_app(
    settings: Settings,
    *,
    engine: Engine | None = None,
    verifier: OidcVerifier | None = None,
    tools: Sequence[Tool[Any]] = (),
) -> Starlette:
    """Build the MCP application for ``settings``, serving ``tools``.

    ``engine`` (the runtime role's pool) and ``verifier`` default to the ones ``settings``
    describe; the default engine is created at start-up and disposed at shutdown. Start-up
    also configures process telemetry, unless an earlier application or entry point in the
    process already did, and starts the SDK's session manager.

    Raises ``ValueError`` when two tools share a name, and ``ConfigurationError`` when the
    ``PF_MCP_*`` settings are missing.
    """
    endpoint = settings.mcp_endpoint()
    registry = {tool.name: tool for tool in tools}
    if len(registry) != len(tools):
        raise ValueError("MCP tool names must be unique")

    def runtime_engine() -> Engine:
        runtime: Engine = app.state.engine
        return runtime

    server = _protocol_server(registry, runtime_engine)
    auth = auth_settings(settings, endpoint)
    protocol_app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        transport_security=transport_security(endpoint),
        auth=auth,
        token_verifier=SharedTokenVerifier(
            verifier or verifier_for(settings),
            runtime_engine,
            clock_skew_seconds=settings.oidc_clock_skew_seconds,
        ),
    )
    # The SDK serves the metadata at the path-inserted form; this copy adds the root form.
    (resource_metadata,) = create_protected_resource_routes(
        resource_url=AnyHttpUrl(endpoint.public_url), authorization_servers=[auth.issuer_url]
    )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        configure_telemetry(settings, "property-focus-mcp")
        # The SDK's transport logs "Terminating session" at INFO after every stateless request;
        # keep only its warnings and errors.
        logging.getLogger(_SDK_TRANSPORT_LOGGER).setLevel(logging.WARNING)
        async with AsyncExitStack() as stack:
            if engine is None:  # an injected engine is set on app.state below
                app.state.engine = await connect_runtime(settings)
                stack.callback(app.state.engine.dispose)
            await stack.enter_async_context(server.session_manager.run())
            yield

    app = Starlette(
        routes=[
            *(_post_only(route) for route in protocol_app.routes),
            Route(
                ROOT_RESOURCE_METADATA_PATH,
                resource_metadata.endpoint,
                methods=["GET", "OPTIONS"],
            ),
        ],
        middleware=[
            Middleware(OpenTelemetryMiddleware),
            Middleware(_RequestScope),
            *protocol_app.user_middleware,
        ],
        lifespan=lifespan,
    )
    if engine is not None:
        app.state.engine = engine
    return app


def auth_settings(settings: Settings, endpoint: McpEndpoint) -> AuthSettings:
    """The SDK's resource-server settings: tokens come from ``PF_OIDC_ISSUER`` for the
    resource at ``PF_MCP_PUBLIC_URL``, and the SDK does not compare a token's resource with
    that URL.

    Keycloak's resource indicators are off, so tokens name the API audience rather than this
    URL; ``oidc.py`` checks that audience instead (GHSA-w4fh-qvv9-3v23).
    """
    return AuthSettings(
        issuer_url=AnyHttpUrl(settings.oidc_issuer),
        resource_server_url=AnyHttpUrl(endpoint.public_url),
        validate_token_resource=False,
    )


def transport_security(endpoint: McpEndpoint) -> TransportSecuritySettings:
    """DNS-rebinding protection that serves only the configured ``Host`` and ``Origin``
    values (``PF_MCP_ALLOWED_HOSTS``, ``PF_MCP_ALLOWED_ORIGINS``)."""
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(endpoint.allowed_hosts),
        allowed_origins=list(endpoint.allowed_origins),
    )


def _post_only(route: BaseRoute) -> BaseRoute:
    """``route`` limited to POST when it serves ``/mcp``.

    A stateless server opens no event stream, so ``GET`` (which the SDK would answer with
    one) and ``DELETE`` (which ends a session) get ``405``.
    """
    if isinstance(route, Route) and route.path == MCP_PATH:
        return Route(MCP_PATH, route.endpoint, methods=["POST"])
    return route


def _protocol_server(
    tools: dict[str, Tool[Any]], engine: Callable[[], Engine]
) -> Server[dict[str, Any]]:
    """The SDK server named ``property-focus`` that lists and calls ``tools``, running them
    on the pool ``engine`` returns."""
    descriptors = [tool.descriptor() for tool in tools.values()]

    async def list_tools(
        ctx: ServerRequestContext[dict[str, Any]], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=descriptors)

    async def call_tool(
        ctx: ServerRequestContext[dict[str, Any]], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        """Run the named tool for the caller. Invalid arguments and a ``CodedError`` from the
        use case are tool errors, which the calling model can act on; an unknown tool is a
        JSON-RPC error. Any other exception is logged and becomes a JSON-RPC internal error
        with a fixed message, so its text never reaches the client in any era."""
        tool = tools.get(params.name)
        if tool is None:
            raise MCPError(types.INVALID_PARAMS, f"Unknown tool {params.name!r}.")
        try:
            arguments = tool.arguments.model_validate(params.arguments or {})
        except ValidationError as error:
            fields = sorted({".".join(str(p) for p in item["loc"]) for item in error.errors()})
            return _tool_error("invalid_input", f"Invalid arguments: {', '.join(fields)}.")
        try:
            result = await asyncio.to_thread(tool.run, _caller(), engine(), arguments)
        except CodedError as error:
            return _tool_error(error.code, error.safe_explanation)
        except Exception:
            logger.exception("MCP tool %s failed", tool.name)
            raise MCPError(_INTERNAL_ERROR, _INTERNAL_ERROR_MESSAGE) from None
        structured = result.model_dump(mode="json")
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(structured))],
            structured_content=structured,
            is_error=False,
        )

    server: Server[dict[str, Any]] = Server(
        "property-focus",
        version=version("property-focus"),
        cache_hints={"server/discover": _CACHE_HINT, "tools/list": _CACHE_HINT},
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    # Starlette's OpenTelemetryMiddleware already traces each request in a server span.
    server.middleware.clear()
    return server


def _caller() -> ExecutionContext:
    """The execution context of the caller whose token the SDK accepted for this request."""
    token = get_access_token()
    if not isinstance(token, CallerAccessToken):
        raise RuntimeError("MCP request served without the shared token check")
    return token.context


def _tool_error(code: str, detail: str) -> types.CallToolResult:
    """A tool result reporting that the tool failed with ``code`` and ``detail``."""
    text = json.dumps({"code": code, "detail": detail})
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


class _RequestScope:
    """Gives each HTTP request a correlation ID, bound to its log lines and span, and answers
    ``503`` with a JSON-RPC error when the token check finds a dependency unavailable."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        correlation_id = uuid.uuid4().hex
        token = _request_correlation_id.set(correlation_id)
        try:
            with bind_correlation_id(correlation_id):
                await self.app(scope, receive, send)
        except IdentityUnavailableError as error:
            body = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": _INTERNAL_ERROR, "message": str(error)},
            }
            await JSONResponse(body, status_code=503)(scope, receive, send)
        finally:
            _request_correlation_id.reset(token)
