"""The REST application's request logging and the ``serve`` command's server options."""

import logging
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine

from property_focus import cli
from property_focus.adapters.auth.oidc import fixture_verifier
from property_focus.adapters.rest.app import create_app
from property_focus.config import Settings

REQUEST_LOGGER = "property_focus.adapters.rest.app"


@pytest.fixture
def settings() -> Settings:
    return Settings.model_validate({"environment": "development", "fixture_issuer_enabled": True})


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    # The probes and paths requested here never connect to the database.
    engine: Engine = create_engine("sqlite://")
    verifier = fixture_verifier(settings, lambda: {"keys": []})
    yield TestClient(create_app(settings, engine=engine, verifier=verifier))
    engine.dispose()


def answered(caplog: pytest.LogCaptureFixture, path: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if getattr(record, "http_path", None) == path]


def test_a_successful_health_probe_is_logged_only_at_debug(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger=REQUEST_LOGGER)

    assert client.get("/health/live").status_code == 200
    assert client.get("/v1/nothing-here").status_code == 404

    assert [record.levelno for record in answered(caplog, "/health/live")] == [logging.DEBUG]
    assert [record.levelno for record in answered(caplog, "/v1/nothing-here")] == [logging.INFO]


def test_serve_turns_off_the_access_log(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **options: calls.append(options))
    monkeypatch.setattr(cli, "configure_telemetry", lambda *arguments: None)
    monkeypatch.setenv("PF_ENVIRONMENT", "development")

    assert cli.main(["serve", "api"]) == 0

    assert len(calls) == 1
    assert calls[0]["access_log"] is False
    assert calls[0]["log_config"] is None
