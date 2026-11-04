"""The dogfood smoke check's cleanup of a client registration that fails its own checks
after the realm answered ``201`` (``backend/scripts/smoke_dogfood.py``).

Every HTTP request is answered in-test; nothing reaches the network.
"""

import importlib.util
import json
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SMOKE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke_dogfood.py"
CLIENT_URI = "https://auth.test/realms/property-focus/clients-registrations/openid-connect/c-1"
COMPLETE = {
    "client_id": "c-1",
    "registration_access_token": "rat",
    "registration_client_uri": CLIENT_URI,
}


def load_smoke_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smoke_dogfood_registration", SMOKE_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = load_smoke_script()


def answer(
    monkeypatch: pytest.MonkeyPatch,
    registration_body: dict[str, Any],
    *,
    cf_ray: bool = True,
    delete_status: int = 204,
) -> list[tuple[str, str]]:
    """Answer the registration ``POST`` with ``201`` and ``registration_body`` (with a
    ``cf-ray`` header when ``cf_ray``) and any ``DELETE`` with ``delete_status``; return
    every request's method and URL."""
    requests: list[tuple[str, str]] = []

    def request(_: object, method: str, url: str, **options: Any) -> Any:
        requests.append((method, url))
        headers = Message()
        if cf_ray:
            headers["cf-ray"] = "8f00"
        if method == "POST":
            return smoke.Response(201, url, headers, json.dumps(registration_body))
        assert options.get("bearer") == "rat"
        return smoke.Response(delete_status, url, headers, "")

    monkeypatch.setattr(smoke.Browser, "request", request)
    return requests


def test_a_complete_registration_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = answer(monkeypatch, COMPLETE)

    registration = smoke.register_mcp_client("https://auth.test")

    assert registration.metadata["client_id"] == "c-1"
    assert [method for method, _ in requests] == ["POST"]


def test_a_201_that_fails_the_edge_check_deletes_its_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    requests = answer(monkeypatch, COMPLETE, cf_ray=False)

    with pytest.raises(smoke.SmokeCheckError, match="no cf-ray header"):
        smoke.register_mcp_client("https://auth.test")

    assert requests[-1] == ("DELETE", CLIENT_URI)
    assert capsys.readouterr().err == ""


def test_a_201_without_the_values_to_delete_it_reports_the_leaked_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    requests = answer(monkeypatch, {"client_id": "c-1", "registration_client_uri": CLIENT_URI})

    with pytest.raises(smoke.SmokeCheckError, match="registration_access_token"):
        smoke.register_mcp_client("https://auth.test")

    assert [method for method, _ in requests] == ["POST"]
    assert "MCP client c-1 was registered but is left in the realm" in capsys.readouterr().err


def test_a_failed_cleanup_delete_reports_the_leaked_client_and_raises_the_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answer(monkeypatch, COMPLETE, cf_ray=False, delete_status=401)

    with pytest.raises(smoke.SmokeCheckError, match="no cf-ray header"):
        smoke.register_mcp_client("https://auth.test")

    assert "MCP client c-1 was registered but is left in the realm" in capsys.readouterr().err
