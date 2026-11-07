"""``make dev`` (backend/scripts/dev.py) migrates the local database before it starts anything."""

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

DEV_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dev.py"
DEVELOPMENT = {
    "PF_ENVIRONMENT": "development",
    "PF_OIDC_ISSUER": "http://localhost:8080/realms/property-focus",
}


def _load_dev() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pf_dev_script", DEV_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ExitedProcess:
    """A started process that has already exited with status 0."""

    def poll(self) -> int:
        return 0

    def terminate(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        pass


@pytest.fixture
def dev(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    for name, value in DEVELOPMENT.items():
        monkeypatch.setenv(name, value)
    return _load_dev()


def test_migrate_runs_before_any_process_starts(
    dev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    def run(command: tuple[str, ...], **_: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(("run", tuple(command)))
        return subprocess.CompletedProcess(command, 0)

    def popen(command: tuple[str, ...], **_: Any) -> _ExitedProcess:
        calls.append(("start", tuple(command)))
        return _ExitedProcess()

    monkeypatch.setattr(dev.subprocess, "run", run)
    monkeypatch.setattr(dev.subprocess, "Popen", popen)

    assert dev.main() == 0
    assert calls[0] == ("run", dev.MIGRATE)
    assert dev.MIGRATE[-1] == "migrate"
    assert [kind for kind, _ in calls[1:]] == ["start"] * len(dev.PROCESSES)


def test_a_failed_migration_starts_nothing_and_returns_its_status(
    dev: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    started: list[tuple[str, ...]] = []

    def run(command: tuple[str, ...], **_: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 3)

    def popen(command: tuple[str, ...], **_: Any) -> _ExitedProcess:
        started.append(tuple(command))
        return _ExitedProcess()

    monkeypatch.setattr(dev.subprocess, "run", run)
    monkeypatch.setattr(dev.subprocess, "Popen", popen)

    assert dev.main() == 3
    assert started == []
    assert capsys.readouterr().err.startswith("make dev: migrate exited with status 3")
