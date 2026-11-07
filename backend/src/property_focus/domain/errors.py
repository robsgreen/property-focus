"""Stable error codes, the failure classes they belong to, and the automatic retry policy.

Every stable code is registered once in ``ERROR_CODES`` with exactly one failure class, or
as a non-failure outcome (an expected result such as ``duplicate``, which reports that a
request replayed an earlier one's idempotency key, or a request error that never becomes
persisted work). The class decides handling:

- ``TRANSIENT``: bounded automatic retry, unless the code ``requires_reconciliation``.
- ``AUTH_REQUIRED``: pause dependent work and notify an operator once.
- ``DATA_UNAVAILABLE``: record ``UNKNOWN``; retried on the invalidation schedule, not here.
- ``INVALID_INPUT``, ``PERMANENT``, ``BUG_UNKNOWN``: never retried automatically.

A code that ``requires_reconciliation`` leaves the provider's outcome uncertain (a send or a
paid call may already have taken effect), so it is never retried automatically: the
``repair`` task asks the provider first and repeats the call only when it reports no effect.
"""

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Literal, get_args

FailureClass = Literal[
    "TRANSIENT", "AUTH_REQUIRED", "DATA_UNAVAILABLE", "INVALID_INPUT", "PERMANENT", "BUG_UNKNOWN"
]
FAILURE_CLASSES: frozenset[FailureClass] = frozenset(get_args(FailureClass))


class UnknownErrorCodeError(LookupError):
    """A code that is not registered in ``ERROR_CODES``."""


@dataclass(frozen=True)
class ErrorCode:
    """One registered stable code.

    ``failure_class`` is None for a non-failure outcome. ``requires_reconciliation`` is only
    valid on a ``TRANSIENT`` code.
    """

    code: str
    failure_class: FailureClass | None
    requires_reconciliation: bool = False

    def __post_init__(self) -> None:
        if self.requires_reconciliation and self.failure_class != "TRANSIENT":
            raise ValueError(f"{self.code}: only TRANSIENT codes can require reconciliation")

    @property
    def is_failure(self) -> bool:
        return self.failure_class is not None

    @property
    def retries_automatically(self) -> bool:
        """True for ``TRANSIENT`` codes whose outcome needs no provider reconciliation."""
        return self.failure_class == "TRANSIENT" and not self.requires_reconciliation


def _registry(*codes: ErrorCode) -> Mapping[str, ErrorCode]:
    registry = {entry.code: entry for entry in codes}
    if len(registry) != len(codes):
        raise ValueError("a stable code is registered more than once")
    return MappingProxyType(registry)


ERROR_CODES: Mapping[str, ErrorCode] = _registry(
    ErrorCode("invalid_input", "INVALID_INPUT"),
    ErrorCode("unsupported_source", "INVALID_INPUT"),
    ErrorCode("unauthorized", None),  # request error, never persisted work
    ErrorCode("forbidden", None),  # request error, never persisted work
    # Request error: the object is absent or the caller may not see it; both look the same.
    ErrorCode("not_found", None),
    ErrorCode("duplicate", None),  # idempotent replay: the earlier request's result
    ErrorCode("idempotency_conflict", "INVALID_INPUT"),
    ErrorCode("material_change_requires_confirmation", None),
    ErrorCode("owner_cannot_leave", None),
    ErrorCode("identity_ambiguous", None),
    ErrorCode("auth_required", "AUTH_REQUIRED"),
    ErrorCode("source_blocked", "AUTH_REQUIRED"),
    ErrorCode("source_changed", "BUG_UNKNOWN"),
    ErrorCode("source_removed", "DATA_UNAVAILABLE"),
    ErrorCode("capability_unavailable", "DATA_UNAVAILABLE"),
    ErrorCode("rate_limited", "TRANSIENT"),
    ErrorCode("dependency_transient", "TRANSIENT"),
    ErrorCode("timeout", "TRANSIENT"),
    ErrorCode("schema_invalid", "PERMANENT"),
    ErrorCode("unsupported_capability", "PERMANENT"),
    ErrorCode("budget_exceeded", None),  # the component is DEFERRED
    ErrorCode("insufficient_data", "DATA_UNAVAILABLE"),
    ErrorCode("evidence_integrity", "BUG_UNKNOWN"),
    ErrorCode("entitlement_revoked", "PERMANENT"),
    ErrorCode("conflicting_claims", None),
    ErrorCode("stale_input", None),
    ErrorCode("notification_unknown", "TRANSIENT", requires_reconciliation=True),
    ErrorCode("internal_defect", "BUG_UNKNOWN"),
    ErrorCode("provider_outcome_unknown", "TRANSIENT", requires_reconciliation=True),
)


def error_code(code: str) -> ErrorCode:
    """Return the registered entry for ``code``; raise ``UnknownErrorCodeError`` otherwise."""
    try:
        return ERROR_CODES[code]
    except KeyError:
        raise UnknownErrorCodeError(f"unregistered error code: {code!r}") from None


class CodedError(Exception):
    """A failure with a registered stable code and an explanation that is safe to show.

    ``safe_explanation`` is shown to users and operators and persisted with failed work, so
    it must never contain secrets or private content; it is also the exception message.
    ``retry_after`` is a provider's requested wait, a floor on the next automatic retry.
    Raises ``UnknownErrorCodeError`` when ``code`` is not registered.
    """

    def __init__(
        self, code: str, safe_explanation: str, *, retry_after: timedelta | None = None
    ) -> None:
        self.error = error_code(code)
        self.safe_explanation = safe_explanation
        self.retry_after = retry_after
        super().__init__(f"{code}: {safe_explanation}")

    @property
    def code(self) -> str:
        return self.error.code


@dataclass(frozen=True)
class RetryPolicy:
    """When to retry a failed attempt automatically.

    Only codes that ``retries_automatically`` are retried, at most ``max_attempts`` attempts
    in total (the first attempt counts), and never past the workflow deadline. The delay
    before retry ``n`` (n = attempts already made) is exponential backoff with equal jitter:
    a uniformly random value in ``[cap / 2, cap]`` where
    ``cap = min(max_delay, base_delay * 2 ** (n - 1))``. A provider ``Retry-After`` is a
    floor on that delay, even above ``max_delay``.
    """

    max_attempts: int = 4
    base_delay: timedelta = timedelta(seconds=2)
    max_delay: timedelta = timedelta(minutes=5)

    def next_attempt_at(
        self,
        code: str,
        *,
        attempts_made: int,
        now: datetime,
        deadline: datetime,
        retry_after: timedelta | None = None,
        rand: Callable[[], float] = random.random,
    ) -> datetime | None:
        """Return when to start the next attempt, or None when no automatic retry applies.

        ``attempts_made`` counts attempts already run, including the one that just failed
        (at least 1). ``now`` and ``deadline`` must both be timezone-aware. ``rand`` returns
        a float in ``[0, 1)`` and exists so tests can fix the jitter. Raises
        ``UnknownErrorCodeError`` for an unregistered code.
        """
        if attempts_made < 1:
            raise ValueError("attempts_made counts the failed attempt, so it is at least 1")
        if not error_code(code).retries_automatically or attempts_made >= self.max_attempts:
            return None

        cap = min(self.max_delay, self.base_delay * 2 ** (attempts_made - 1))
        delay = cap / 2 + cap / 2 * rand()
        if retry_after is not None:
            delay = max(delay, retry_after)

        scheduled = now + delay
        return scheduled if scheduled < deadline else None
