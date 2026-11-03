"""Runtime manifests: no long-running workers (D26), a scheduler crontab of registered kinds,
the dogfood Keycloak image, and ``make dev``'s refusal of non-development configuration.

The compose files are read through ``docker compose config``, the parser that runs them, so
anchors, merges and profiles resolve exactly as in a deployment. It needs only the Docker
CLI, not a running daemon.
"""

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from property_focus.runtime.scheduled import SCHEDULED_KINDS
from property_focus.runtime.scheduler import CrontabError, parse_crontab

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = [
    REPO_ROOT / "compose.yaml",
    REPO_ROOT / "infrastructure" / "dogfood" / "compose.yaml",
]
CRONTAB = REPO_ROOT / "infrastructure" / "dogfood" / "scheduler.crontab"
BACKEND_DOCKERFILE = REPO_ROOT / "infrastructure" / "docker" / "backend.Dockerfile"
KEYCLOAK_DOCKERFILE = REPO_ROOT / "infrastructure" / "docker" / "keycloak.Dockerfile"
DEV_SCRIPT = REPO_ROOT / "backend" / "scripts" / "dev.py"

CLI = ("python", "-m", "property_focus.cli")
# Where the backend image's scheduler stage puts the dogfood crontab.
IMAGE_CRONTAB = "/app/scheduler.crontab"
# A shell loop in a service command: the shape of a worker that waits for work.
SHELL_LOOP = re.compile(r"\b(while|until)\b.*\bdo\b|\bfor\b.*\bin\b.*\bdo\b", re.DOTALL)
# Words that name polling or queue work, which only a one-off task process may do.
WORKER_WORDS = re.compile(r"\b(worker|workers|queue|queues|mailbox|imap|poll|consume)\b")


def compose_services(path: Path) -> dict[str, dict[str, Any]]:
    """Every service of the compose file at ``path``, in all profiles, as compose resolves it."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--file",
            str(path),
            "--profile",
            "*",
            "config",
            "--format",
            "json",
            "--no-interpolate",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    services: dict[str, dict[str, Any]] = json.loads(result.stdout)["services"]
    return services


def backend_command(service: dict[str, Any]) -> list[str] | None:
    """The CLI arguments of a service built from the backend image, after the CLI prefix;
    None for any other service."""
    build = service.get("build") or {}
    if not str(build.get("dockerfile", "")).endswith("backend.Dockerfile"):
        return None
    argv = [*(service.get("entrypoint") or []), *(service.get("command") or [])]
    return argv[len(CLI) :] if tuple(argv[: len(CLI)]) == CLI else argv


def worker_problems(services: dict[str, dict[str, Any]]) -> list[str]:
    """One message per service that would run as a long-running worker.

    Long-running servers (Postgres, Keycloak, the API, the MCP adapter, the scheduler) and
    one-shot steps are fine. A service fails when its command loops in a shell, when compose
    would restart a ``run-task`` process forever, when a long-running command names queue or
    mailbox work, or when a backend-image service runs anything but ``serve``, ``schedule``,
    ``migrate`` or a one-off ``run-task <kind>``.
    """
    problems = []
    for name, service in sorted(services.items()):
        argv = [*(service.get("entrypoint") or []), *(service.get("command") or [])]
        text = " ".join(argv)
        restarts = service.get("restart", "no") != "no"
        one_off_task = "run-task" in argv and not restarts
        if SHELL_LOOP.search(text):
            problems.append(f"{name}: the command loops in a shell")
        if "run-task" in argv and restarts:
            problems.append(f"{name}: compose restarts a run-task process, which makes it a loop")
        if WORKER_WORDS.search(text) and not one_off_task:
            problems.append(f"{name}: a long-running command names queue or mailbox work")
        command = backend_command(service)
        if command is not None and not _allowed_backend_command(command):
            problems.append(f"{name}: the backend image may not run {command}")
    return problems


def _allowed_backend_command(command: list[str]) -> bool:
    match command:
        case ["serve", "api" | "mcp", *_] | ["schedule", "--crontab", _] | ["migrate"]:
            return True
        case ["run-task", _]:  # a one-off run; worker_problems checks its restart policy
            return True
    return False


def crontab_problems(text: str) -> list[str]:
    """One message per crontab entry that is not ``python -m property_focus.cli run-task
    <kind>`` for a kind registered in ``runtime/scheduled.py``."""
    try:
        entries = parse_crontab(text)
    except CrontabError as error:
        return [str(error)]
    problems = []
    for entry in entries:
        match entry.command:
            case (*prefix, "run-task", kind) if tuple(prefix) == CLI:
                if kind not in SCHEDULED_KINDS:
                    problems.append(f"line {entry.line}: {kind!r} is not a registered kind")
            case _:
                problems.append(f"line {entry.line}: not `{' '.join(CLI)} run-task <kind>`")
    return problems


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda path: str(path.relative_to(REPO_ROOT)))
def test_compose_files_define_no_long_running_worker(path: Path) -> None:
    assert worker_problems(compose_services(path)) == []


def test_crontab_runs_only_registered_kinds_through_run_task() -> None:
    assert crontab_problems(CRONTAB.read_text()) == []


def test_crontab_schedules_every_registered_kind() -> None:
    kinds = {entry.command[-1] for entry in parse_crontab(CRONTAB.read_text())}

    assert kinds == set(SCHEDULED_KINDS)


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda path: str(path.relative_to(REPO_ROOT)))
def test_scheduler_service_runs_the_dogfood_crontab(path: Path) -> None:
    scheduler = compose_services(path)["scheduler"]

    assert backend_command(scheduler) == ["schedule", "--crontab", IMAGE_CRONTAB]
    assert (
        f"COPY {CRONTAB.relative_to(REPO_ROOT)} {IMAGE_CRONTAB}" in BACKEND_DOCKERFILE.read_text()
    )


def test_flags_a_looping_worker_and_a_restarted_task(tmp_path: Path) -> None:
    manifest = tmp_path / "compose.yaml"
    manifest.write_text(
        """
services:
  mail-worker:
    image: busybox
    command: ["sh", "-c", "while true; do property-focus run-task imap-poll; sleep 20; done"]
  sweeper:
    image: busybox
    command: ["property-focus", "run-task", "outbox-sweep"]
    restart: always
  db-init:
    image: busybox
    command: ["psql", "--file=/init.sql"]
    restart: "no"
  backend-worker:
    build: {context: ., dockerfile: infrastructure/docker/backend.Dockerfile}
    command: ["python", "-m", "property_focus.cli", "consume", "--queue", "tasks"]
"""
    )

    problems = worker_problems(compose_services(manifest))

    assert problems == [
        "backend-worker: a long-running command names queue or mailbox work",
        "backend-worker: the backend image may not run ['consume', '--queue', 'tasks']",
        "mail-worker: the command loops in a shell",
        "mail-worker: a long-running command names queue or mailbox work",
        "sweeper: compose restarts a run-task process, which makes it a loop",
    ]


def test_flags_crontab_entries_that_are_not_registered_run_task_kinds() -> None:
    crontab = (
        "* * * * * python -m property_focus.cli run-task outbox-sweep\n"
        "* * * * * python -m property_focus.cli run-task mailbox-forever\n"
        "* * * * * sh -c 'property-focus run-task repair'\n"
    )

    assert crontab_problems(crontab) == [
        "line 2: 'mailbox-forever' is not a registered kind",
        "line 3: not `python -m property_focus.cli run-task <kind>`",
    ]


def test_dogfood_keycloak_image_matches_the_local_pin_without_experimental_features() -> None:
    compose_image = compose_services(COMPOSE_FILES[0])["keycloak"]["image"]
    dockerfile = KEYCLOAK_DOCKERFILE.read_text()
    bases = re.findall(r"^FROM (\S+)", dockerfile, re.MULTILINE)
    dogfood_keycloak = compose_services(COMPOSE_FILES[1])["keycloak"]

    assert bases == [compose_image, compose_image]
    assert "--features" not in dockerfile
    assert "KC_FEATURES" not in json.dumps(dogfood_keycloak)
    assert dogfood_keycloak["command"] == ["start", "--optimized", "--import-realm"]


@pytest.mark.parametrize(
    "environment",
    [
        pytest.param({}, id="unset"),
        pytest.param(
            {"PF_ENVIRONMENT": "dogfood", "PF_OIDC_ISSUER": "https://auth.example.test/realms/x"},
            id="dogfood",
        ),
    ],
)
def test_make_dev_refuses_non_development_configuration(environment: dict[str, str]) -> None:
    result = subprocess.run(
        [sys.executable, DEV_SCRIPT],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 2
    assert result.stderr.startswith("make dev: ")
