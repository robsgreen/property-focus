"""The Docker Compose minimum-version check (infrastructure/docker/check-compose-version.sh)
that `make install` and dogfood's deploy.sh run, against a stand-in `docker` that prints a
given `docker compose version` output."""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECK_SCRIPT = REPO_ROOT / "infrastructure" / "docker" / "check-compose-version.sh"
MAKEFILE = REPO_ROOT / "Makefile"
UPDATE_MESSAGE = "update Docker Desktop / the compose plugin"


def docker_reporting(directory: Path, output: str | None) -> dict[str, str]:
    """An environment whose ``docker compose version`` prints ``output``, or fails like a
    Docker without the Compose plugin when ``output`` is None."""
    bin_dir = directory / "bin"
    bin_dir.mkdir()
    reply = "exit 1" if output is None else f"echo '{output}'"
    docker = bin_dir / "docker"
    docker.write_text(f'#!/bin/sh\n[ "$*" = "compose version" ] || exit 97\n{reply}\n')
    docker.chmod(0o755)
    return {"PATH": f"{bin_dir}:/usr/bin:/bin"}


def run(command: list[str], environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, env=environment, capture_output=True, text=True, check=False, timeout=60
    )


@pytest.mark.parametrize(
    "output",
    [
        "Docker Compose version v2.27.2",
        "Docker Compose version v2.40.3",
        "Docker Compose version v2.40.3-desktop.1",
        "2.40.3-desktop.1",
        "Docker Compose version v5.6.1",
    ],
)
def test_accepts_the_minimum_compose_and_newer(tmp_path: Path, output: str) -> None:
    result = run(["bash", str(CHECK_SCRIPT)], docker_reporting(tmp_path, output))

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("output", "found"),
    [
        # The version a developer's Docker Desktop reported when inline configs failed.
        pytest.param("Docker Compose version v2.7.0", "2.7.0", id="v2.7.0"),
        pytest.param("Docker Compose version v2.27.1", "2.27.1", id="one-patch-short"),
        pytest.param("2.26.1-desktop.1", "2.26.1", id="desktop-short"),
        pytest.param(None, "none", id="no-compose-plugin"),
    ],
)
def test_rejects_an_older_compose_naming_both_versions(
    tmp_path: Path, output: str | None, found: str
) -> None:
    result = run(["bash", str(CHECK_SCRIPT)], docker_reporting(tmp_path, output))

    assert result.returncode == 1
    assert result.stderr.strip() == (
        f"Docker Compose >= 2.27.2 is required, found {found} — {UPDATE_MESSAGE}"
    )


def test_make_install_stops_at_an_old_compose(tmp_path: Path) -> None:
    environment = docker_reporting(tmp_path, "Docker Compose version v2.7.0")

    result = run(["make", "--no-print-directory", "-f", str(MAKEFILE), "install"], environment)

    assert result.returncode != 0
    assert "found 2.7.0" in result.stderr
    assert "pnpm install" not in result.stdout
