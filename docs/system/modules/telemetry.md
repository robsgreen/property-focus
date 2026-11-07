# Telemetry

`adapters/telemetry.py` gives every backend process the same observability: OpenTelemetry traces and metrics exported over OTLP/HTTP, structured JSON logs on standard error, a correlation ID on every log line, and redaction of secrets and personal data before anything leaves the process (D50, D49). Traces continue across asynchronous hops, so one trace follows a request through every task it causes.

## Key files

| File | Role |
|---|---|
| `backend/src/property_focus/adapters/telemetry.py` | `configure_telemetry()`, `bind_correlation_id()`, `current_trace_context()`, `resume_trace()`, `register_secret()`, `redact_text()`, `redact_attribute()`, `is_sensitive_key()`, `JsonLogFormatter`, `add_span_exporter()`, `flush_telemetry()` |
| `infrastructure/dogfood/otel-collector.yaml` | Dogfood collector: OTLP/HTTP in, OpenObserve out |
| `compose.yaml` (`otel`) | Local collector on `127.0.0.1:4318` |
| `backend/tests/integration/test_telemetry.py`, `unit/test_redaction.py` | End-to-end trace continuity and redaction; one key rule for fields and free text |

## Process set-up

`configure_telemetry(settings, service_name)` takes effect once per process; later calls do nothing, so each app factory and CLI entry point calls it freely. It installs:

- A tracer provider and meter provider with resource attributes `service.name`, `service.version` (the package version) and `deployment.environment.name`.
- When `PF_OTLP_ENDPOINT` is set: a batch span exporter to `<endpoint>/v1/traces` behind the redacting processor, and a periodic metric exporter to `<endpoint>/v1/metrics`. Each export times out after 3 seconds and a refused batch is dropped, so export never blocks a request. Without an endpoint nothing is exported.
- A root log handler at `INFO` that writes one JSON object per line to standard error.

Service names: `property-focus-api`, `property-focus-mcp`, `property-focus-scheduler`, and `property-focus-<application>` from `cli serve`. Short-lived processes call `flush_telemetry()` before exiting. `add_span_exporter()` attaches an extra (redacted) exporter; tests use it.

The REST app disables FastAPI's own OpenTelemetry auto-configuration and log export; the MCP app uses Starlette's `OpenTelemetryMiddleware`. Both rely on the providers above.

## Log lines

```json
{"timestamp": "2026-11-02T18:00:00.123+00:00", "level": "INFO", "logger": "property_focus.adapters.rest.app",
 "message": "GET /v1/workflow-runs/6f1c... answered 200", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
 "span_id": "00f067aa0ba902b7", "correlation_id": "4b9e1f0c2d7a4e8b9c3d5f6a7b8c9d0e",
 "http_method": "GET", "http_path": "/v1/workflow-runs/6f1c...", "http_status": 200, "duration_ms": 12.4}
```

The REST app logs one such line per request, at `DEBUG` for a successful health probe (so the `INFO` handler drops it) and `INFO` otherwise; uvicorn's access log is off. Fields: `timestamp` (UTC, milliseconds), `level`, `logger`, `message`, `trace_id` and `span_id` (null outside a span), `correlation_id` (null when none is bound), `exception` and `stack` when present, and every `extra` field. `bind_correlation_id(id)` sets the correlation ID for the block through a context variable and records it on the current span.

## Redaction

Applied to every log line and to a copy of every span (attributes, event attributes, status description) before export:

| Rule | Covers |
|---|---|
| Registered secrets | Every value a `SecretProvider` returned in this process (`register_secret`), wherever it appears in free text. Values shorter than 6 characters are not scrubbed from free text |
| Sensitive keys (`is_sensitive_key`) | Keys whose words (split on punctuation and camelCase; up to three adjacent words are also joined, so `passWord` counts as `password`) include `password`, `secret`, `token`, `authorization`, `cookie`, `session`, `jwt`, `apikey`, `credential(s)`, `signature`, `passphrase`, `passwd`, `body`, `financial`, `income`, `salary`, `ssn`, `iban`, or whose snake-case form contains `api_key`, `account_number`, `routing_number`, `card_number`, `credit_card`, `credit_score`, `bank_account`, `net_worth`, `down_payment`, `tax_id`, `walk_away`, `hard_limit`, `code_verifier` |
| Free-text patterns (`redact_text`) | `Bearer`/`Basic` credentials, JSON Web Tokens (`eyJ…`), `Cookie`/`Set-Cookie` lines, the password in a URL's user information (`scheme://user:password@host`), and `key=value`, `key: value` or `"key": "value"` pairs whose key `is_sensitive_key` accepts, so a key is redacted in free text exactly when it is redacted as a field. Every pair in the text is examined, including query parameters inside a URL and a pair that follows another `key:`; only a sensitive key's value is replaced. A key is the whole run of letters, digits, `_`, `.` and `-` before the separator (`--password`, `x-api-key`) |

Redacted values become `[REDACTED]`. Redaction runs in time linear in the text, whatever it contains: it runs synchronously on the request path over client-controlled values (path, query string, `User-Agent`), so every pattern starts only at the beginning of a run of the characters it scans and never rescans them. A crafted 1 MB line redacts in under about a second (worst measured shape 0.87 s; `unit/test_redaction.py` checks the time stays linear). The server caps request headers at 16 KB, so redacting a real request's values costs milliseconds. A JSON Web Token directly preceded by a word character or `-` is not recognized as one.

## Trace continuity

`current_trace_context()` returns the current span's W3C `traceparent` (and `tracestate`). `record_event()` and admission store it on `outbox_events.trace_context` and `workflow_runs.trace_context`. Later, `resume_trace(name, context, attributes=...)` opens a span that is a child of the recorded context and also links to it. The outbox publisher uses it for `publish <kind>` spans and `run_task` for `task <kind>` spans, so the request span, publish spans and task spans share one trace ID.

## Where telemetry goes

| Environment | Collector | Backend |
|---|---|---|
| Local | Compose `otel` (`otel/opentelemetry-collector-contrib`) on `127.0.0.1:4318` | The collector's debug exporter (`docker compose logs otel`) |
| Dogfood | `otel-collector` service | Self-hosted OpenObserve, UI on loopback port 5080 ([operations](../../operations.md)) |
| AWS | Planned (CloudWatch, phase 9) | |

Logs stay on standard error in every environment; dogfood rotates them at five 10 MB files per container.

## Design decisions

- Redaction lives in the process, before export, so no collector or backend ever receives a secret or private value.
- One `configure_telemetry` per process keeps every entry point identical and makes double configuration harmless.
- Trace context is persisted with the work rather than passed through launch payloads, because `TaskInput` is untrusted and minimal.

## Related docs

- [Configuration and secrets](./configuration-and-secrets.md) for `PF_OTLP_ENDPOINT` and secret registration
- [Workflows and tasks](./workflows-and-tasks.md) for where traces resume
- [REST API](./rest-api.md) for request logs
