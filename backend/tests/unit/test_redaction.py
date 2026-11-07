"""Redaction: one rule decides which keys are sensitive, for structured fields and free text."""

import random
import time
import uuid

import pytest

from property_focus.adapters.telemetry import REDACTED, redact_attribute, redact_text

SENSITIVE_KEYS = [
    "password",
    "access_token",
    "refresh_token",
    "client_secret",
    "api_key",
    "apiKey",
    "session_id",
    "authorization",
    "code_verifier",
    "income",
    "salary",
    "ssn",
    "iban",
    "credit_card",
    "net_worth",
    "account_number",
    "passWord",
    "PassWord",
    "userPassWord",
]
VALUE = "plain-value-123"


@pytest.mark.parametrize("key", SENSITIVE_KEYS)
def test_a_sensitive_key_is_redacted_as_a_field_and_inside_text(key: str) -> None:
    assert redact_attribute(key, VALUE) == REDACTED
    for text in (f"{key}={VALUE}", f"{key}: {VALUE}", f'{{"{key}": "{VALUE}"}}'):
        redacted = redact_text(f"request {text} sent")
        assert VALUE not in redacted, text
        assert REDACTED in redacted


@pytest.mark.parametrize(
    "key", ["page", "kind", "workflow_run_id", "http", "userAgent", "passCount", "wordList"]
)
def test_other_keys_keep_their_values(key: str) -> None:
    assert redact_attribute(key, VALUE) == VALUE
    assert redact_text(f"{key}={VALUE}&{key}: {VALUE}") == f"{key}={VALUE}&{key}: {VALUE}"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("Authorization: Bearer abc.def-ghi", "abc.def-ghi"),
        ("sent Basic dXNlcjpwYXNz to the proxy", "dXNlcjpwYXNz"),
        ("token eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln in a message", "eyJzdWIiOiJ4In0"),
        ("Set-Cookie: sid=abc123; Path=/", "sid=abc123"),
    ],
)
def test_credentials_in_text_are_redacted(text: str, secret: str) -> None:
    assert secret not in redact_text(text)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("url https://x/cb?token=t1&state=s", "t1"),
        ("failed: password=hunter2", "hunter2"),
        ("postgresql://u:pw@h/db?password=x1", "x1"),
        ("GET http://h/p?access_token=abc", "abc"),
        ("detail:code_verifier=v1", "v1"),
        ("callback https://h/cb?state=s&code=c&refresh_token=r9&page=2", "r9"),
        ('login failed {"user": "ana", "password": "hunter2"}', "hunter2"),
        ("connect postgresql://u:secret@h/db failed", "secret"),
        ("ran migrate --password=hunter2", "hunter2"),
        ("header 2fa-token: t0k3n sent", "t0k3n"),
        ("café-password=hunter2", "hunter2"),
    ],
)
def test_a_sensitive_pair_is_redacted_wherever_it_appears_in_text(text: str, secret: str) -> None:
    assert secret not in redact_text(text)


def test_only_the_sensitive_value_in_a_url_is_redacted() -> None:
    assert (
        redact_text("url https://x/cb?state=s&token=t1&page=2 failed")
        == f"url https://x/cb?state=s&token={REDACTED}&page=2 failed"
    )
    assert (
        redact_text("connect postgresql://u:secret@h/db?sslmode=require")
        == f"connect postgresql://u:{REDACTED}@h/db?sslmode=require"
    )


_RANDOM = random.Random(0)
# About 1 MB of random UUIDs joined by dashes: many segments start with a letter.
UUID_LINE = "-".join(str(uuid.UUID(int=_RANDOM.getrandbits(128))) for _ in range(27_000))


@pytest.mark.parametrize(
    "text",
    [
        "a-" * 500_000,
        "a=" * 500_000,
        "a+" * 500_000,
        "a." * 500_000,
        "a:" * 500_000,
        "eyJ-" * 250_000,
        "eyJa." * 200_000,
        UUID_LINE,
        "/v1/" + "a-" * 500_000,
        "password=" * 100_000,
        "password=a&" * 90_000,
        "https://u:" + "a-" * 500_000,
        "GET /v1/workflow-runs?" + "&".join(f"page{n}=x-{n}" for n in range(60_000)),
        "Mozilla/5.0 (X11; Linux x86_64) " * 30_000,
    ],
    ids=lambda text: f"{text[:12]!r}x{len(text)}",
)
def test_redaction_takes_linear_time_on_long_crafted_text(text: str) -> None:
    # Client-controlled headers and paths are redacted on the request path, so a crafted
    # megabyte must not hold the event loop.
    started = time.perf_counter()
    redact_text(text)
    assert time.perf_counter() - started < 1.0
