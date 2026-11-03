"""Repository checks: layer import boundaries, credential scan and root layout."""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BOUNDARY_CHECK = REPO_ROOT / "backend" / "scripts" / "check_boundaries.py"
SECRET_CHECK = REPO_ROOT / "backend" / "scripts" / "check_fixture_secrets.py"

# Built by concatenation so this file never contains a token-shaped literal itself.
FAKE_GITHUB_TOKEN = "ghp" + "_" + "Synthetic0Test0Token0Value0123456789ab"
FAKE_PRIVATE_KEY = (
    "-----BEGIN "
    + "RSA PRIVATE KEY-----\nMIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Qu\n"
)


def run(*command: str | Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(part) for part in command], cwd=cwd, capture_output=True, text=True, check=False
    )


def write_files(root: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def check_boundaries(src: Path) -> subprocess.CompletedProcess[str]:
    return run(sys.executable, BOUNDARY_CHECK, "--src", src)


def check_secrets(root: Path) -> subprocess.CompletedProcess[str]:
    return run(sys.executable, SECRET_CHECK, "--root", root)


def git_tree(root: Path, files: dict[str, str]) -> Path:
    run("git", "init", "-q", root)
    write_files(root, files)
    return root


class TestBoundaryCheck:
    def test_layered_imports_that_follow_the_rules_pass(self, tmp_path: Path) -> None:
        write_files(
            tmp_path,
            {
                "property_focus/domain/__init__.py": "",
                "property_focus/domain/claims.py": "from . import identity\nimport datetime\n",
                "property_focus/domain/identity.py": "",
                "property_focus/application/ports.py": (
                    "from property_focus.domain import claims\n"
                    "from property_focus.adapters.db import repositories\n"
                    "from property_focus.adapters.db.models import User\n"
                    "from property_focus.adapters.telemetry import resume_trace\n"
                ),
                "property_focus/adapters/rest/app.py": "import fastapi\n",
            },
        )

        result = check_boundaries(tmp_path)

        assert result.returncode == 0, result.stderr

    @pytest.mark.parametrize(
        ("source", "forbidden"),
        [
            ("from property_focus.adapters.db import models\n", "property_focus.adapters.db"),
            ("from property_focus import runtime\n", "property_focus.runtime"),
            ("from ..adapters import secrets\n", "property_focus.adapters"),
            ("import fastapi\n", "fastapi"),
            ("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import boto3\n", "boto3"),
            ("from playwright.sync_api import Page\n", "playwright.sync_api"),
        ],
    )
    def test_domain_importing_an_adapter_runtime_or_framework_fails(
        self, tmp_path: Path, source: str, forbidden: str
    ) -> None:
        write_files(tmp_path, {"property_focus/domain/identity.py": source})

        result = check_boundaries(tmp_path)

        assert result.returncode == 1
        assert f"property_focus.domain must not import {forbidden}" in result.stderr
        assert "identity.py:" in result.stderr

    @pytest.mark.parametrize(
        "source",
        [
            "from property_focus.adapters.rest import schemas\n",
            "import property_focus.adapters.mcp.server\n",
            "from ..adapters import rest\n",
            "from property_focus.adapters.auth.oidc import authenticate\n",
            "from property_focus.adapters.secrets import secret_provider_for\n",
            "import property_focus.adapters\n",
            "from property_focus.adapters.dbx import models\n",
        ],
    )
    def test_application_importing_an_adapter_other_than_db_or_telemetry_fails(
        self, tmp_path: Path, source: str
    ) -> None:
        write_files(tmp_path, {"property_focus/application/evaluation.py": source})

        result = check_boundaries(tmp_path)

        assert result.returncode == 1
        assert "property_focus.application must not import property_focus.adapters" in (
            result.stderr
        )

    def test_similarly_named_packages_are_not_mistaken_for_forbidden_ones(
        self, tmp_path: Path
    ) -> None:
        write_files(tmp_path, {"property_focus/domain/identity.py": "import fastapitools\n"})

        assert check_boundaries(tmp_path).returncode == 0


class TestSecretCheck:
    def test_tree_with_only_placeholders_passes(self, tmp_path: Path) -> None:
        root = git_tree(
            tmp_path,
            {
                ".env.example": "PF_DATA_MODE=synthetic\n# PF_OPENAI_API_KEY=\n",
                "config.yaml": 'api_key: "your-api-key-goes-here"\n',
                "realm.json": '{"password": "${SMTP_PASSWORD_FROM_DEPLOYMENT:}"}\n',
                "fixtures/mail/listing.eml": "From: agent@example.com\nSubject: New listing\n",
            },
        )

        result = check_secrets(root)

        assert result.returncode == 0, result.stderr

    def test_planted_token_in_a_committed_file_fails_without_printing_the_value(
        self, tmp_path: Path
    ) -> None:
        root = git_tree(tmp_path, {"backend/settings.py": f'TOKEN = "{FAKE_GITHUB_TOKEN}"\n'})

        result = check_secrets(root)

        assert result.returncode == 1
        assert "backend/settings.py:1: GitHub token" in result.stderr
        assert FAKE_GITHUB_TOKEN not in result.stderr + result.stdout

    def test_planted_secret_in_an_ignored_fixture_still_fails(self, tmp_path: Path) -> None:
        root = git_tree(
            tmp_path,
            {
                ".gitignore": "fixtures/evidence/\n",
                "fixtures/evidence/capture.txt": "header\n" + FAKE_PRIVATE_KEY,
            },
        )

        result = check_secrets(root)

        assert result.returncode == 1
        assert "fixtures/evidence/capture.txt:2: private key" in result.stderr

    @pytest.mark.parametrize(
        ("content", "pattern_name"),
        [
            ('{"cookies": ' + '[{"name": "session", "value": "abc"}]}', "browser session state"),
            ("Set-Cookie: session=Q2hhbmdlZEJ5VGVzdDEyMzQ1Ng; Path=/", "cookie header"),
            ("aws_key = " + "AKIA" + "SYNTHETIC0TEST00", "AWS access key ID"),
            ("imap_password = " + '"Tr0ub4dor-and-3-more-words"', "credential assignment"),
            ('"password": ' + '"${SMTP_PASSWORD:Tr0ub4dor-and-3}"', "credential assignment"),
        ],
    )
    def test_cookie_key_and_password_patterns_are_detected(
        self, tmp_path: Path, content: str, pattern_name: str
    ) -> None:
        root = git_tree(tmp_path, {"fixtures/themls/page.txt": content + "\n"})

        result = check_secrets(root)

        assert result.returncode == 1
        assert f"fixtures/themls/page.txt:1: {pattern_name}" in result.stderr

    def test_root_outside_a_git_work_tree_is_a_usage_error(self, tmp_path: Path) -> None:
        result = run(sys.executable, SECRET_CHECK, "--root", tmp_path, cwd=tmp_path)

        assert result.returncode == 2
        assert "not a Git work tree" in result.stderr


class TestLayoutCheck:
    def run_check_layout(self, root: Path) -> subprocess.CompletedProcess[str]:
        return run(
            "make", "--no-print-directory", "-f", REPO_ROOT / "Makefile", "check-layout", cwd=root
        )

    def test_d47_layout_passes(self, tmp_path: Path) -> None:
        for directory in ("backend", "frontend", "infrastructure", "ontology", "fixtures"):
            (tmp_path / directory).mkdir()

        assert self.run_check_layout(tmp_path).returncode == 0

    @pytest.mark.parametrize("directory", ["evals", "apps", "packages", "infra"])
    def test_forbidden_root_directory_fails(self, tmp_path: Path, directory: str) -> None:
        (tmp_path / directory).mkdir()

        result = self.run_check_layout(tmp_path)

        assert result.returncode != 0
        assert f"root '{directory}/' is not allowed" in result.stderr
