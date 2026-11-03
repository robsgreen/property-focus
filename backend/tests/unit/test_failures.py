"""Failure taxonomy: every stable code is classified, and only safe transient codes retry."""

from datetime import UTC, datetime, timedelta

import pytest

from property_focus.domain.errors import (
    ERROR_CODES,
    FAILURE_CLASSES,
    ErrorCode,
    RetryPolicy,
    UnknownErrorCodeError,
    error_code,
)

# The stable codes the error-handling table defines. Adding a code to the table means adding
# it here and registering it with a class (or as a non-failure) in ERROR_CODES. `not_found`
# is the request error behind the API's 404 for absent and inaccessible IDs.
STABLE_CODES = {
    "invalid_input",
    "unsupported_source",
    "unauthorized",
    "forbidden",
    "not_found",
    "duplicate",
    "idempotency_conflict",
    "material_change_requires_confirmation",
    "owner_cannot_leave",
    "identity_ambiguous",
    "auth_required",
    "source_blocked",
    "source_changed",
    "source_removed",
    "capability_unavailable",
    "rate_limited",
    "dependency_transient",
    "timeout",
    "schema_invalid",
    "unsupported_capability",
    "budget_exceeded",
    "insufficient_data",
    "evidence_integrity",
    "entitlement_revoked",
    "conflicting_claims",
    "stale_input",
    "notification_unknown",
    "internal_defect",
    "provider_outcome_unknown",
}
RECONCILIATION_CODES = {"notification_unknown", "provider_outcome_unknown"}
NEVER_RETRIED_CLASSES = sorted(FAILURE_CLASSES - {"TRANSIENT"})

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
FAR_DEADLINE = NOW + timedelta(hours=1)
POLICY = RetryPolicy()


def next_attempt(
    code: str,
    attempts_made: int = 1,
    *,
    deadline: datetime = FAR_DEADLINE,
    retry_after: timedelta | None = None,
    jitter: float = 0.0,
) -> datetime | None:
    return POLICY.next_attempt_at(
        code,
        attempts_made=attempts_made,
        now=NOW,
        deadline=deadline,
        retry_after=retry_after,
        rand=lambda: jitter,
    )


class TestTaxonomy:
    def test_every_stable_code_is_registered_and_nothing_else(self) -> None:
        assert set(ERROR_CODES) == STABLE_CODES

    @pytest.mark.parametrize("code", sorted(STABLE_CODES))
    def test_each_code_has_one_class_or_is_a_non_failure(self, code: str) -> None:
        entry = error_code(code)

        assert entry.code == code
        assert entry.failure_class is None or entry.failure_class in FAILURE_CLASSES

    def test_an_unmapped_code_fails(self) -> None:
        with pytest.raises(UnknownErrorCodeError, match="not_a_code"):
            error_code("not_a_code")

    def test_only_provider_outcome_codes_require_reconciliation(self) -> None:
        flagged = {code for code, entry in ERROR_CODES.items() if entry.requires_reconciliation}

        assert flagged == RECONCILIATION_CODES
        assert all(ERROR_CODES[code].failure_class == "TRANSIENT" for code in flagged)

    def test_reconciliation_flag_is_rejected_on_a_non_transient_code(self) -> None:
        with pytest.raises(ValueError, match="only TRANSIENT"):
            ErrorCode("example", "PERMANENT", requires_reconciliation=True)

    def test_request_errors_and_expected_outcomes_are_non_failures(self) -> None:
        for code in ("unauthorized", "forbidden", "duplicate", "budget_exceeded"):
            assert not error_code(code).is_failure


class TestRetryPolicy:
    def test_transient_code_retries_after_backoff(self) -> None:
        assert next_attempt("dependency_transient", jitter=0.0) == NOW + timedelta(seconds=1)
        assert next_attempt("dependency_transient", jitter=0.5) == NOW + timedelta(seconds=1.5)

    def test_backoff_doubles_with_each_attempt(self) -> None:
        delays = [next_attempt("timeout", attempts, jitter=0.0) for attempts in (1, 2, 3)]

        assert delays == [NOW + timedelta(seconds=seconds) for seconds in (1, 2, 4)]

    def test_backoff_is_capped_at_the_maximum_delay(self) -> None:
        policy = RetryPolicy(max_attempts=10, max_delay=timedelta(seconds=10))

        scheduled = policy.next_attempt_at(
            "timeout", attempts_made=9, now=NOW, deadline=FAR_DEADLINE, rand=lambda: 0.999
        )

        assert scheduled is not None
        assert scheduled - NOW <= timedelta(seconds=10)

    def test_jitter_stays_within_half_to_full_backoff(self) -> None:
        low = next_attempt("rate_limited", 3, jitter=0.0)
        high = next_attempt("rate_limited", 3, jitter=0.999999)

        assert low == NOW + timedelta(seconds=4)
        assert high is not None
        assert NOW + timedelta(seconds=4) < high < NOW + timedelta(seconds=8)

    def test_at_most_four_attempts_including_the_first(self) -> None:
        assert next_attempt("dependency_transient", 3) is not None
        assert next_attempt("dependency_transient", 4) is None

    def test_retry_after_is_honored_when_longer_than_backoff(self) -> None:
        scheduled = next_attempt("rate_limited", retry_after=timedelta(seconds=90))

        assert scheduled == NOW + timedelta(seconds=90)

    def test_retry_after_shorter_than_backoff_does_not_shorten_it(self) -> None:
        scheduled = next_attempt("rate_limited", 3, retry_after=timedelta(seconds=1))

        assert scheduled == NOW + timedelta(seconds=4)

    def test_no_retry_scheduled_past_the_workflow_deadline(self) -> None:
        deadline = NOW + timedelta(seconds=30)

        assert next_attempt("rate_limited", deadline=deadline) is not None
        assert (
            next_attempt("rate_limited", deadline=deadline, retry_after=timedelta(minutes=1))
            is None
        )

    @pytest.mark.parametrize("code", sorted(RECONCILIATION_CODES))
    def test_flagged_codes_schedule_no_automatic_retry(self, code: str) -> None:
        assert error_code(code).failure_class == "TRANSIENT"
        assert next_attempt(code) is None

    @pytest.mark.parametrize("failure_class", NEVER_RETRIED_CLASSES)
    def test_non_transient_classes_never_retry(self, failure_class: str) -> None:
        codes = [
            code for code, entry in ERROR_CODES.items() if entry.failure_class == failure_class
        ]

        assert codes, f"no registered code exercises {failure_class}"
        for code in codes:
            assert next_attempt(code) is None

    def test_non_failure_codes_never_retry(self) -> None:
        for code, entry in ERROR_CODES.items():
            if not entry.is_failure:
                assert next_attempt(code) is None

    def test_unregistered_code_fails_rather_than_defaulting(self) -> None:
        with pytest.raises(UnknownErrorCodeError):
            next_attempt("not_a_code")

    def test_attempts_made_counts_the_failed_attempt(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            next_attempt("timeout", 0)
