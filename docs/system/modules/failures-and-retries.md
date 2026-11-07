# Failures and Retries

Every failure the system reports carries a stable error code from one registry. The code's failure class decides what happens next: an automatic retry, a pause for a person, or a final failure. The same codes appear in REST problem responses, MCP tool errors and persisted workflow steps.

## Key files

| File | Role |
|---|---|
| `backend/src/property_focus/domain/errors.py` | `ERROR_CODES`, `ErrorCode`, `CodedError`, `RetryPolicy`, `error_code()` |
| `backend/src/property_focus/application/workflows.py` | `failure_effect()`, `FailureEffect`: applies the class to a workflow run |
| `backend/src/property_focus/adapters/rest/app.py` | `_STATUS_BY_CODE`: HTTP status per code |
| `backend/tests/unit/test_failures.py` | Registry and retry-policy tests |

## Failure classes

| Class | Handling |
|---|---|
| `TRANSIENT` | Bounded automatic retry, unless the code `requires_reconciliation` |
| `AUTH_REQUIRED` | The workflow run moves to `WAITING_FOR_USER` |
| `DATA_UNAVAILABLE` | Record `UNKNOWN`; retried on the invalidation schedule, not by the retry policy |
| `INVALID_INPUT`, `PERMANENT`, `BUG_UNKNOWN` | Never retried automatically; the run fails |

A code with class `None` is a non-failure outcome: an expected result (`duplicate`, which reports an idempotent replay of an earlier request; `conflicting_claims`; `stale_input`), or a request error that never becomes persisted work (`unauthorized`, `forbidden`, `not_found`).

## The registry

`ERROR_CODES` maps each code to exactly one `ErrorCode(code, failure_class, requires_reconciliation)`. Registering a code twice raises at import time, and `requires_reconciliation` is valid only on `TRANSIENT` codes.

| Code | Class | | Code | Class |
|---|---|---|---|---|
| `invalid_input` | INVALID_INPUT | | `rate_limited` | TRANSIENT |
| `unsupported_source` | INVALID_INPUT | | `dependency_transient` | TRANSIENT |
| `idempotency_conflict` | INVALID_INPUT | | `timeout` | TRANSIENT |
| `unauthorized` | none | | `notification_unknown` | TRANSIENT, reconcile |
| `forbidden` | none | | `provider_outcome_unknown` | TRANSIENT, reconcile |
| `not_found` | none | | `schema_invalid` | PERMANENT |
| `duplicate` | none | | `unsupported_capability` | PERMANENT |
| `material_change_requires_confirmation` | none | | `entitlement_revoked` | PERMANENT |
| `owner_cannot_leave` | none | | `source_changed` | BUG_UNKNOWN |
| `identity_ambiguous` | none | | `evidence_integrity` | BUG_UNKNOWN |
| `auth_required` | AUTH_REQUIRED | | `internal_defect` | BUG_UNKNOWN |
| `source_blocked` | AUTH_REQUIRED | | `source_removed` | DATA_UNAVAILABLE |
| `budget_exceeded` | none (component DEFERRED) | | `capability_unavailable` | DATA_UNAVAILABLE |
| `conflicting_claims` | none | | `insufficient_data` | DATA_UNAVAILABLE |
| `stale_input` | none | | | |

A reconciliation code means the provider may already have acted (a sent notification, a paid model call). The retry policy never repeats it; the `repair` path must ask the provider first.

## Raising a failure

```python
raise CodedError("rate_limited", "The listing source asked us to slow down.", retry_after=timedelta(seconds=30))
```

`CodedError(code, safe_explanation, *, retry_after=None)` raises `UnknownErrorCodeError` for an unregistered code. The safe explanation is shown to users and operators and persisted on the failed step, so it must never contain secrets or private content. `retry_after` is a provider's requested wait.

## Retry policy

`RetryPolicy(max_attempts=4, base_delay=2s, max_delay=5min)`. `next_attempt_at(code, attempts_made, now, deadline, retry_after)` returns `None` unless the code `retries_automatically` (TRANSIENT without reconciliation) and fewer than `max_attempts` attempts ran. The delay before retry `n` is exponential backoff with equal jitter: uniform in `[cap/2, cap]` where `cap = min(max_delay, base_delay * 2**(n-1))`. A `retry_after` is a floor on the delay, even above `max_delay`. A retry that would land at or after the run's deadline is dropped.

`failure_effect()` turns that into a run outcome: `AUTH_REQUIRED` gives `WAITING_FOR_USER`; a scheduled retry keeps the run `RUNNING` with its lease expiring at the retry time; anything else gives `FAILED`. See [workflows and tasks](./workflows-and-tasks.md).

The policy's delay is the earliest time a retry may start, not when it starts. Nothing wakes a run at its retry time: the `repair` scheduled task relaunches it on its first pass after that time, and repair runs every 5 minutes. A short delay (2, 4 or 8 seconds) therefore becomes a wait of up to one repair interval, so retry granularity is bounded by the repair cadence. Only a delay longer than the interval, such as a long `retry_after`, changes when the retry starts.

## HTTP mapping

The REST API maps a `CodedError` to a status through `_STATUS_BY_CODE`; an unmapped code gets 503 when `TRANSIENT`, else 500. A database that cannot be reached while a request authenticates is `dependency_transient` (REST `503`, MCP HTTP `503`), so clients retry it.

| Status | Codes |
|---|---|
| 401 | `unauthorized` |
| 403 | `forbidden` |
| 404 | `not_found` |
| 409 | `idempotency_conflict`, `material_change_requires_confirmation`, `owner_cannot_leave` |
| 422 | `invalid_input`, `unsupported_source` |
| 429 | `rate_limited`, `budget_exceeded` |
| 503 | `dependency_transient`, `timeout` |

A malformed request is `400 invalid_input` from FastAPI validation, not from this table.

## Design decisions

- One registry, one class per code, so every adapter and the workflow engine agree on what a failure means.
- Reconciliation codes exist because a blind retry of a send or a paid call can double its effect.
- The domain module is pure: it imports no adapter, runtime, FastAPI or boto3 code (`check_boundaries.py`).

## Related docs

- [Workflows and tasks](./workflows-and-tasks.md)
- [REST API](./rest-api.md) for the problem document
- [MCP adapter](./mcp-adapter.md) for tool errors
