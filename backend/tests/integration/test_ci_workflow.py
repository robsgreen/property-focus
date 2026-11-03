"""The pull-request workflow in ``.github/workflows/ci.yml``: which jobs a change runs, and
that its install step stops a manifest change made without the matching lockfile update.

Path selection runs the workflow's own ``changes`` script with bash over a real ``git diff``
in a scratch repository, then applies each job's ``if`` condition, so the test follows the
workflow file rather than a copy of its rules. The lockfile cases run the root Makefile's
``install`` target, the step every check job starts with, on a scratch copy of the committed
manifests and lockfiles.
"""

import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
MAKEFILE = REPO_ROOT / "Makefile"

ALWAYS_RUN = {"changes", "secret-scan"}
BACKEND_JOBS = {"backend", "integration", "replay", "generation", "images", "bootstrap"}
EVERY_JOB = ALWAYS_RUN | BACKEND_JOBS | {"frontend", "cdk", "markdown"}
BACKEND_IMAGE_TARGETS = ["task", "api", "mcp", "scheduler"]
CHECK_JOBS = ["backend", "integration", "replay", "generation", "frontend", "cdk"]
MANIFESTS_AND_LOCKS = [
    "package.json",
    "pnpm-workspace.yaml",
    "pnpm-lock.yaml",
    "frontend/package.json",
    "infrastructure/cdk/package.json",
    "pyproject.toml",
    "uv.lock",
    "backend/pyproject.toml",
]
JOB_HEADER = re.compile(r"^  ([a-z][a-z0-9-]*):\s*$")
CHANGES_CONDITION = re.compile(r"needs\.changes\.outputs\.([a-z-]+) == 'true'")


def workflow_jobs() -> dict[str, list[str]]:
    """The lines of each job under ``jobs:``, by job name."""
    lines = CI_WORKFLOW.read_text().splitlines()
    jobs: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in lines[lines.index("jobs:") + 1 :]:
        header = JOB_HEADER.match(line)
        if header:
            current = jobs.setdefault(header.group(1), [])
        elif current is not None:
            current.append(line)
    return jobs


def block_after(lines: list[str], marker: str) -> str:
    """The literal block of the first ``run: |`` that follows the line ``marker``."""
    start = next(index for index, line in enumerate(lines) if line.strip() == marker)
    run = next(index for index in range(start, len(lines)) if lines[index].strip() == "run: |")
    indent = len(lines[run]) - len(lines[run].lstrip())
    body: list[str] = []
    for line in lines[run + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) <= indent:
            break
        body.append(line)
    return textwrap.dedent("\n".join(body))


def run_commands(job_lines: list[str]) -> list[str]:
    """The one-line ``run:`` commands of a job, in step order."""
    return [
        line.split("run:", 1)[1].strip()
        for line in job_lines
        if line.strip().startswith("run:") and line.strip() != "run: |"
    ]


def output_conditions() -> dict[str, str | None]:
    """For each job, the ``changes`` output its ``if`` needs, or None when it always runs."""
    conditions: dict[str, str | None] = {}
    for job, lines in workflow_jobs().items():
        condition = next((line for line in lines if line.strip().startswith("if:")), None)
        if condition is None:
            conditions[job] = None
            continue
        match = CHANGES_CONDITION.search(condition)
        assert match is not None, f"{job}: unexpected condition {condition.strip()}"
        conditions[job] = match.group(1)
    return conditions


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=ci-test", "-c", "user.email=ci-test@example.test", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def write(repo: Path, path: str, content: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def jobs_that_run(
    tmp_path: Path,
    edits: dict[str, str],
    *,
    moves: dict[str, str] | None = None,
    event: str = "pull_request",
) -> set[str]:
    """Run the ``changes`` script for a head commit that applies ``edits`` and ``moves`` to a
    base commit, and return the jobs whose conditions then hold."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    for path in ["README.md", *edits, *(moves or {})]:
        write(repo, path, "base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    for path, content in edits.items():
        write(repo, path, content)
    for source, destination in (moves or {}).items():
        (repo / destination).parent.mkdir(parents=True, exist_ok=True)
        git(repo, "mv", source, destination)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", "head")
    head = git(repo, "rev-parse", "HEAD")

    outputs = tmp_path / "github-output"
    script = block_after(workflow_jobs()["changes"], "id: filter")
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        cwd=repo,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "EVENT_NAME": event,
            "BASE_SHA": base,
            "HEAD_SHA": head,
            "GITHUB_OUTPUT": str(outputs),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    selected = {
        name
        for name, value in (line.split("=", 1) for line in outputs.read_text().split())
        if value == "true"
    }
    return {
        job for job, output in output_conditions().items() if output is None or output in selected
    }


def test_each_conditional_job_waits_on_its_own_changes_output() -> None:
    conditions = output_conditions()

    assert set(conditions) == EVERY_JOB
    assert {job for job, output in conditions.items() if output is None} == ALWAYS_RUN
    assert all(output == job for job, output in conditions.items() if output is not None)


@pytest.mark.parametrize(
    "edits",
    [
        pytest.param({"docs/operations.md": "changed\n"}, id="docs"),
        pytest.param({"specs/property-focus/master-plan.md": "changed\n"}, id="specs"),
        pytest.param(
            {"docs/compatibility.md": "changed\n", "specs/property-focus/notes.md": "x\n"},
            id="docs-and-specs",
        ),
    ],
)
def test_docs_only_pull_request_runs_only_the_secret_scan_and_markdown_lint(
    tmp_path: Path, edits: dict[str, str]
) -> None:
    assert jobs_that_run(tmp_path, edits) == ALWAYS_RUN | {"markdown"}


def test_backend_pull_request_runs_the_backend_generation_and_replay_jobs(
    tmp_path: Path,
) -> None:
    jobs = jobs_that_run(tmp_path, {"backend/src/property_focus/cli.py": "changed\n"})

    assert jobs == ALWAYS_RUN | BACKEND_JOBS


def test_frontend_pull_request_runs_the_web_and_generation_jobs(tmp_path: Path) -> None:
    jobs = jobs_that_run(tmp_path, {"frontend/src/main.tsx": "changed\n"})

    assert jobs == ALWAYS_RUN | {"frontend", "generation"}


def test_realm_change_runs_the_realm_tests_and_image_build_but_not_cdk(tmp_path: Path) -> None:
    jobs = jobs_that_run(tmp_path, {"infrastructure/keycloak/realm-property-focus.json": "x\n"})

    assert jobs == ALWAYS_RUN | {"integration", "images", "bootstrap"}


@pytest.mark.parametrize(
    "path", ["infrastructure/docker/backend.Dockerfile", "infrastructure/dogfood/compose.yaml"]
)
def test_image_or_dogfood_change_runs_the_manifest_tests_and_image_build_but_not_cdk(
    tmp_path: Path, path: str
) -> None:
    jobs = jobs_that_run(tmp_path, {path: "changed\n"})

    assert jobs == ALWAYS_RUN | {"backend", "integration", "images"}


def test_cdk_change_runs_only_the_cdk_job(tmp_path: Path) -> None:
    jobs = jobs_that_run(tmp_path, {"infrastructure/cdk/lib/identity-stack.ts": "changed\n"})

    assert jobs == ALWAYS_RUN | {"cdk"}


def test_images_job_builds_every_dogfood_image_and_validates_the_dogfood_compose_file() -> None:
    lines = workflow_jobs()["images"]
    build_loop = block_after(lines, "- name: Build the backend image targets")
    commands = run_commands(lines)

    assert f"for target in {' '.join(BACKEND_IMAGE_TARGETS)}; do" in build_loop
    assert '--file infrastructure/docker/backend.Dockerfile --target "$target"' in build_loop
    assert any("--file infrastructure/docker/keycloak.Dockerfile" in line for line in commands)
    assert (
        "docker compose --file infrastructure/dogfood/compose.yaml --profile tasks config --quiet"
        in commands
    )


@pytest.mark.parametrize(
    "path", ["uv.lock", "pnpm-lock.yaml", "Makefile", "compose.yaml", ".github/workflows/ci.yml"]
)
def test_root_tooling_change_runs_every_job(tmp_path: Path, path: str) -> None:
    assert jobs_that_run(tmp_path, {path: "changed\n"}) == EVERY_JOB


def test_moving_a_file_out_of_backend_still_runs_the_backend_jobs(tmp_path: Path) -> None:
    jobs = jobs_that_run(tmp_path, {}, moves={"backend/notes.md": "docs/notes.md"})

    assert jobs >= BACKEND_JOBS


def test_docs_and_backend_change_runs_the_union(tmp_path: Path) -> None:
    jobs = jobs_that_run(
        tmp_path, {"docs/operations.md": "changed\n", "backend/scripts/dev.py": "changed\n"}
    )

    assert jobs == ALWAYS_RUN | BACKEND_JOBS | {"markdown"}


def test_push_to_main_runs_every_job(tmp_path: Path) -> None:
    jobs = jobs_that_run(tmp_path, {"docs/operations.md": "changed\n"}, event="push")

    assert jobs == EVERY_JOB


@pytest.mark.parametrize("job", CHECK_JOBS)
def test_every_check_job_installs_from_the_lockfiles_before_checking(job: str) -> None:
    commands = run_commands(workflow_jobs()[job])
    make_steps = [command for command in commands if command.startswith("make ")]

    assert make_steps[0] == "make install", commands
    assert len(make_steps) > 1


def test_markdown_job_installs_from_the_lockfile_before_linting() -> None:
    commands = run_commands(workflow_jobs()["markdown"])

    assert commands.index("pnpm install --frozen-lockfile") < commands.index("pnpm lint:md")


@pytest.fixture
def manifest_copy(tmp_path: Path) -> Path:
    """A scratch directory holding the committed manifests and lockfiles."""
    for relative in MANIFESTS_AND_LOCKS:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / relative, target)
    return tmp_path


def lock_check(root: Path, *command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(command), cwd=root, capture_output=True, text=True, check=False)


def make_install(root: Path) -> subprocess.CompletedProcess[str]:
    return lock_check(root, "make", "--no-print-directory", "-f", str(MAKEFILE), "install")


def drop_line(path: Path, line: str) -> None:
    text = path.read_text()
    assert line in text
    path.write_text(text.replace(line, "", 1))


def test_committed_lockfiles_match_their_manifests(manifest_copy: Path) -> None:
    pnpm = lock_check(manifest_copy, "pnpm", "install", "--frozen-lockfile", "--lockfile-only")
    uv = lock_check(manifest_copy, "uv", "lock", "--check", "--offline")

    assert pnpm.returncode == 0, pnpm.stdout + pnpm.stderr
    assert uv.returncode == 0, uv.stderr


def test_python_manifest_change_without_lock_update_fails_install(manifest_copy: Path) -> None:
    drop_line(manifest_copy / "backend" / "pyproject.toml", '    "uvicorn==0.54.0",\n')

    result = make_install(manifest_copy)

    assert result.returncode != 0
    assert "uv.lock` needs to be updated" in result.stderr


@pytest.mark.parametrize(
    ("manifest", "line"),
    [
        pytest.param("frontend/package.json", '    "react-router": "7.18.4"', id="web-app"),
        pytest.param("package.json", '"markdownlint-cli2": "0.24.0"', id="root"),
    ],
)
def test_node_manifest_change_without_lock_update_fails_install(
    manifest_copy: Path, manifest: str, line: str
) -> None:
    path = manifest_copy / manifest
    path.write_text(path.read_text().replace(line, line.replace('": "', '": "0.0.1-'), 1))

    result = make_install(manifest_copy)

    assert result.returncode != 0
    assert "ERR_PNPM_OUTDATED_LOCKFILE" in result.stdout + result.stderr
