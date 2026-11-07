# REST API

The REST API is a FastAPI application that serves the web app. Today it exposes health probes and one authenticated read: a workflow run of the caller's tenant. Every error is an `application/problem+json` document with a stable code and a correlation ID. Its OpenAPI document is committed and drives the generated web client.

## Key files

| File | Role |
|---|---|
| `backend/src/property_focus/adapters/rest/app.py` | `create_app()`, `CorrelationIdMiddleware`, `problem_response()`, exception handlers, `_STATUS_BY_CODE`, OpenAPI post-processing |
| `backend/src/property_focus/adapters/rest/routes.py` | `health` and `v1` routers, `execution_context` dependency, `PROBLEM_RESPONSES` |
| `backend/src/property_focus/adapters/rest/schemas.py` | `Problem`, `Health`, `WorkflowRun` (with `from_rows()`), `WorkflowStep` response models; state and tier fields reuse the `Literal` types in `adapters/db/models.py` |
| `backend/scripts/export_openapi.py` | Writes `frontend/src/api/openapi.json` from the app without starting it |
| `backend/tests/contract/test_api.py`, `test_token_rejection.py`, `unit/test_export_openapi.py` | Contract and export tests |

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health/live` | none | `getLiveness`: `{"status": "ok"}` while the process serves requests |
| GET | `/health/ready` | none | `getReadiness`: `{"status": "ok"}` when `SELECT 1` succeeds; `503 dependency_transient` otherwise |
| GET | `/v1/workflow-runs/{run_id}` | Bearer | `getWorkflowRun`: the run with its steps (`result_refs` are ID strings), or `404 not_found` for a missing run and for another tenant's run, with the same body |

Every `/v1` route depends on `execution_context`, which rejects a request without a `Bearer` credential (`401 unauthorized`, "Sign in to continue.") and otherwise calls `oidc.authenticate()` ([identity and authentication](./identity-and-auth.md)). A missing database there is `503 dependency_transient`. The route calls the application use case (`read_workflow_run(engine, context, run_id)`, which reads in `context.scope`) and returns `WorkflowRun.from_rows(run, steps)`; an MCP tool for the same read calls the same two functions. The tenant always comes from the token.

## Application factory

`create_app(settings, *, engine=None, verifier=None)`:

- On start-up, calls `configure_telemetry(settings, "property-focus-api")` and opens the runtime-role pool with `connect_runtime(settings)` (5 connections, disposed at shutdown). An injected `engine` skips this and is never disposed.
- Sets `app.state.verifier` to `verifier_for(settings)` unless one is injected.
- Disables FastAPI's own OpenTelemetry auto-configuration and log export, so spans leave only through the redacting providers.
- Adds `CorrelationIdMiddleware` and the exception handlers, includes both routers, and post-processes the OpenAPI document.

The CLI serves it with `property-focus serve api` (uvicorn, `127.0.0.1:8000` by default, `proxy_headers=True`, access log off because it records query strings).

## Correlation IDs and request logs

`CorrelationIdMiddleware` keeps an inbound `X-Correlation-ID` when it matches `[A-Za-z0-9._:-]{1,128}` and otherwise generates a new hex ID. It stores it on `request.state.correlation_id`, binds it to log lines, echoes it in the response header, and logs one line per answered request with method, path (never the query string), status and duration: at `INFO`, except a health probe below `500`, which is logged at `DEBUG` (below the process's `INFO` threshold) because orchestrators repeat it every few seconds.

## Problem responses

```json
{
  "type": "urn:property-focus:problem:not_found",
  "title": "Not Found",
  "status": 404,
  "code": "not_found",
  "failure_class": null,
  "detail": "No workflow run has this ID.",
  "correlation_id": "4b9e1f0c2d7a4e8b9c3d5f6a7b8c9d0e",
  "retryable": false
}
```

| Raised | Status and code |
|---|---|
| `CodedError` | From `_STATUS_BY_CODE`, else 503 for `TRANSIENT`, else 500; `detail` is the safe explanation ([failures and retries](./failures-and-retries.md)) |
| FastAPI `RequestValidationError` | `400 invalid_input`, naming the invalid fields |
| Starlette `HTTPException` 404 | `404 not_found` |
| Other `HTTPException` below 500 | Same status, `invalid_input` |
| Any other exception | `500 internal_defect`, fixed detail; the exception is logged with the correlation ID and never returned |

`retryable` mirrors `ErrorCode.retries_automatically`. A `401` adds `WWW-Authenticate: Bearer`. A `retry_after` on the error adds `Retry-After` and `retry_after_seconds`.

## OpenAPI and the generated client

`_document_problem_responses` rewrites the generated document so error responses use `application/problem+json`, drops FastAPI's default `422` entries and the `HTTPValidationError`/`ValidationError` schemas. `export_openapi.py` builds the app with fixed development settings and writes sorted, indented JSON, so an unchanged API regenerates byte for byte.

After any schema change run `make generate-api` and commit both `frontend/src/api/openapi.json` and `frontend/src/api/generated.ts`; `make check-generated` (CI `generation` job) fails when either differs. Never edit `generated.ts` by hand.

## Design decisions

- A missing run and another tenant's run return the same `404`, so the API never confirms that an ID exists elsewhere.
- Error details are safe explanations by construction; unexpected exceptions never reach the client.
- The REST API and the MCP adapter share `authenticate()` and the use cases; neither calls the other.

## Related docs

- [Identity and authentication](./identity-and-auth.md)
- [Workflows and tasks](./workflows-and-tasks.md) for `read_workflow_run()`
- [Web app](./web-app.md) for the generated RTK Query client
- [Telemetry](./telemetry.md)
