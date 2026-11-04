# MCP Adapter

The MCP adapter lets any Model Context Protocol client reach Property Focus use cases on behalf of a signed-in person. It runs the official MCP Python SDK (`mcp` 2.3.x, pinned to the minor version): the SDK's low-level `Server` and its streamable HTTP transport, stateless with JSON responses, mounted at `POST /mcp` in our Starlette application. The SDK owns JSON-RPC, both protocol eras, version negotiation and the 2026-07-28 header checks; the adapter supplies the tools, the shared token check, transport security settings and the protected-resource metadata. It ships with no product tools yet.

## Key files

| File | Role |
|---|---|
| `backend/src/property_focus/adapters/mcp/server.py` | `create_app()`, `Tool`, `SharedTokenVerifier`, `CallerAccessToken`, `IdentityUnavailableError`, `auth_settings()`, `transport_security()` |
| `backend/src/property_focus/adapters/auth/oidc.py` | `authenticate()`, the token check REST uses too |
| `backend/src/property_focus/config.py` | `PF_MCP_*` settings and `Settings.mcp_endpoint()` |
| `backend/tests/contract/test_mcp.py`, `contract/mcp_client.py` | Specification-level flows against Keycloak through a generic client: discovery, dynamic registration, PKCE sign-in, both eras, parity with REST |
| `backend/tests/contract/test_client_neutrality.py` | Identical answers whatever client identifies itself |
| `backend/tests/unit/test_mcp_adapter.py` | The token verifier and its audience, the SDK settings, the `Host`/`Origin` check before sign-in, the tool registry, structured tool errors and the generic internal error |
| `backend/tests/contract/test_issuer_failures.py` | `503`, not `401`, when the issuer's key set or discovery document fails |
| `backend/tests/integration/test_security_dependencies.py` | The `mcp` pin stays inside `>=2.3,<2.4` and the lockfile resolves inside it |

## Routes

| Method | Path | Description |
|---|---|---|
| POST | `/mcp` | One JSON-RPC 2.0 message per request; one `application/json` response |
| GET | `/.well-known/oauth-protected-resource/mcp` | RFC 9728 metadata (the SDK's path-inserted form): `resource` is `PF_MCP_PUBLIC_URL`, `authorization_servers` is `[PF_OIDC_ISSUER]`, `bearer_methods_supported` is `["header"]` |
| GET | `/.well-known/oauth-protected-resource` | The same document at the root form, for clients that look there |

`GET` and `DELETE` on `/mcp` get `405`. The CLI serves the app with `property-focus serve mcp` (default `127.0.0.1:8001`). On dogfood, Caddy routes `/mcp` and `/.well-known/oauth-protected-resource*` on the app host to the adapter and passes the original `Host` through; every URL the adapter publishes comes from `PF_MCP_PUBLIC_URL`, not from the request.

Clients of revision 2025-03-26 skip protected-resource metadata and look for authorization-server metadata at the root of the MCP server's origin (`/.well-known/oauth-authorization-server`). The adapter does not serve it. On dogfood, Caddy answers that path on the app host with the realm's own metadata, fetched from Keycloak as the auth host, so its `issuer` is `PF_OIDC_ISSUER` and every endpoint is on the auth host; those clients discover, register and sign in like later ones. Locally (`make dev`) the adapter and Keycloak listen on different ports, so a 2025-03-26 client finds no metadata on the adapter's origin and needs the issuer configured.

## Request handling

Each request passes these layers, outermost first:

1. Starlette's `OpenTelemetryMiddleware` opens the request's server span. The SDK's own per-message span (`Server.middleware`) is turned off, so a request is traced once.
2. `_RequestScope` generates a correlation ID and binds it to log lines and the span. When the token check raises `IdentityUnavailableError` it answers `503` with JSON-RPC error `-32603` and no challenge.
3. `_TransportSecurity` refuses a `Host` outside `PF_MCP_ALLOWED_HOSTS` (`421`) and an `Origin` outside `PF_MCP_ALLOWED_ORIGINS` (`403`), with the SDK's own check (`TransportSecurityMiddleware`), on every path. It runs before authentication, so a request from a foreign host or origin never reaches the token check and never provisions a user or tenant, with or without a valid token. A request without `Origin` (not a browser) passes.
4. The SDK's bearer authentication calls `SharedTokenVerifier.verify_token()`, which calls `oidc.authenticate()` with `PF_MCP_AUDIENCE`: the same verification and tenant provisioning as REST, for the MCP audience. A token for the REST API's audience (the web app's) is refused like any invalid token. A valid token becomes a `CallerAccessToken` carrying the caller's `ExecutionContext` (its required `client_id` is a constant nobody reads; scopes come from the token's `scope`; `expires_at` is `exp` plus `PF_OIDC_CLOCK_SKEW_SECONDS`, because the SDK checks expiry again without leeway). An invalid token returns none. `dependency_transient` (issuer key set or database unavailable) raises `IdentityUnavailableError`.
5. Without a valid token the SDK answers `401` with `WWW-Authenticate: Bearer error="invalid_token", error_description="…", resource_metadata="<origin of PF_MCP_PUBLIC_URL>/.well-known/oauth-protected-resource/mcp"` and a JSON body. It sends `error="invalid_token"` even when no token was presented.
6. The SDK's transport repeats the `Host` and `Origin` check (harmless; it cannot fail after step 3), and a POST whose `Content-Type` is not `application/json` gets `400`.
7. The SDK transport serves the message: a notification gets `202` with no body; a request gets one `application/json` response. No `Mcp-Session-Id` is issued and no event stream is opened, so any replica can answer any request.

`AuthSettings` names `PF_OIDC_ISSUER` as issuer and `PF_MCP_PUBLIC_URL` as resource server URL, and sets `validate_token_resource=False` explicitly: Keycloak's resource indicators are off, so tokens carry the MCP audience (`property-focus-mcp`, `PF_MCP_AUDIENCE`), not the MCP URL, and `oidc.py` checks that audience (GHSA-w4fh-qvv9-3v23).

**Tier 1 change (PR #2 review): MCP accepts a new audience.** The endpoint accepts only tokens whose `aud` names `property-focus-mcp`, which the realm gives every dynamically registered client by default (realm default client scope `property-focus-mcp`); tokens for `property-focus-api` (the web client's) get `401 invalid_token`. Before this change MCP accepted `property-focus-api`. An MCP client registered earlier keeps working once `keycloak_realm.py apply` moves it to the new scope; its next sign-in or token refresh carries the new audience. Client information in `initialize` or `_meta` is never read.

## Protocol eras

The SDK chooses the era from the `MCP-Protocol-Version` header alone:

| | Legacy | Modern |
|---|---|---|
| Revisions | `2025-11-25`, `2025-06-18`, `2025-03-26`, `2024-11-05` | `2026-07-28` |
| Selected when | The header is absent (treated as `2025-03-26`) or names one of these revisions; `params._meta` is then ignored | The header names any other value |
| Checks | `initialize` needs `protocolVersion`, `capabilities` and `clientInfo` (`-32602` otherwise) and answers the requested revision, or `2025-11-25` for a newer or unknown one | `params._meta` must carry `io.modelcontextprotocol/protocolVersion` and `io.modelcontextprotocol/clientCapabilities` (`400 -32602` otherwise); the header must equal the `_meta` version, `Mcp-Method` the method, and `Mcp-Name` the target of `tools/call`, `prompts/get` and `resources/read`, Base64 form `=?base64?…?=` decoded (`400 -32020` otherwise); a `_meta` version the SDK does not serve is `400 -32022` with `data.supported` (`["2026-07-28"]`) and `data.requested` |
| Methods | `initialize`, `ping`, `tools/list`, `tools/call` | `server/discover`, `tools/list`, `tools/call`; every result carries `resultType: "complete"` and the server's identity in `_meta`; discovery and tool lists carry `ttlMs: 300000` and `cacheScope: "private"` |
| Unknown method | `-32601` | `404 -32601` (including the removed `ping` and `logging/setLevel`) |

Revision 2024-11-05 predates MCP authorization; it is served because the SDK serves it, and it still needs a valid bearer token. Capabilities in every era: `{"tools": {"listChanged": false}}`. Malformed bodies: not JSON is `400 -32700`; a JSON-RPC batch is `400 -32600` on the modern path and `400 -32602` on the legacy path. Message texts are the SDK's and are not part of the contract.

## Tools

```python
Tool(
    name="get_example",
    description="...",
    arguments=ExampleArguments,   # pydantic model; its JSON schema is inputSchema
    result=ExampleResult,         # pydantic model; its JSON schema is outputSchema
    run=lambda context, engine, arguments: ExampleResult(...),
)
```

`create_app(settings, tools=..., engine=None, verifier=None)` takes the tool list; names must be unique. The SDK's `on_list_tools` handler lists them; its `on_call_tool` handler validates arguments with the tool's model, runs `run` in a worker thread with the caller's `ExecutionContext` (from the request's `CallerAccessToken`) and the engine, and returns `content` (the result as JSON text) plus `structuredContent`. Invalid arguments (code `invalid_input`) and a `CodedError` from the use case become a tool result with `isError: true` and text `{"code": ..., "detail": ...}`, which the calling model can act on; an unknown tool name is JSON-RPC `-32602`. Any other exception from a tool is logged (with redaction) and answered in every protocol era with JSON-RPC `-32603` and the fixed message `Internal server error`, so its text never reaches the client. Tools call application use cases directly, never the REST API. `cli serve mcp` passes no tools today.

## Start-up

`create_app()` reads `settings.mcp_endpoint()`, which raises `ConfigurationError` when a `PF_MCP_*` value is unset outside development. The lifespan configures telemetry (`configure_telemetry(settings, "property-focus-mcp")`), sets the SDK transport logger `mcp.server.streamable_http` to `WARNING` (it logs "Terminating session" at `INFO` after every stateless request), creates the runtime-role pool with `connect_runtime(settings)` (5 connections; an injected engine is never disposed) and enters the SDK session manager's `run()`, which the transport needs. `cli serve mcp` turns uvicorn's access log off, as for the API.

## Design decisions

- The official SDK, not hand-written protocol code (A6): it follows each MCP revision's wire changes and security fixes. `mcp` is pinned to `>=2.3,<2.4`; `test_mcp.py` is the conformance gate for each minor upgrade, done in its own change.
- The SDK's wire behavior is accepted where it differs from the earlier hand-written adapter: 2024-11-05 is served (sign-in still required), a modern request without its `_meta` envelope is `-32602`, and the `401` challenge is the SDK's format. Contract tests assert the specification's codes and fields, not message texts.
- Stateless, JSON-only responses so the adapter runs on any replica and later on Lambda.
- One endpoint for both eras, decided per request, so old and new clients work without configuration.
- Authorization discovery through standard metadata (for 2025-03-26 clients on dogfood, the realm's metadata at the app host's root) and anonymous dynamic client registration; Client ID Metadata Documents are deferred (option C) until Keycloak's support leaves experimental status.
- No client-specific behavior anywhere, enforced by `test_client_neutrality.py`.

## Related docs

- [Identity and authentication](./identity-and-auth.md) for the token check and the realm's registration policies
- [Configuration and secrets](./configuration-and-secrets.md) for the `PF_MCP_*` settings
- [Failures and retries](./failures-and-retries.md) for tool error codes
- [REST API](./rest-api.md)
