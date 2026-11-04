"""Security scan wiring: Semgrep suppressions, the warn-only workflow and Bandit skips.

The committed suppression list must keep the two findings that were triaged as real
(the OIDC urlopen use and the pnpm supply-chain rules) and drop only the named false
positives. The workflow file is checked as text, the way the CI path-filter test reads
its workflow.
"""

import importlib.util
import json
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "backend" / "scripts" / "filter_semgrep_sarif.py"
SUPPRESSIONS = REPO_ROOT / "backend" / "scripts" / "semgrep_suppressions.toml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "security.yml"
MAKEFILE = REPO_ROOT / "Makefile"
PYPROJECT = REPO_ROOT / "pyproject.toml"

SQLALCHEMY = "python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text"
JWT = "python.jwt.security.unverified-jwt-decode.unverified-jwt-decode"
MISSING_USER = "dockerfile.security.missing-user.missing-user"
MISSING_ENTRYPOINT = "dockerfile.security.missing-user-entrypoint.missing-user-entrypoint"
MIGRATION = "backend/migrations/versions/0001_foundation.py"
SERVER = "backend/src/property_focus/adapters/mcp/server.py"
KEYCLOAK = "infrastructure/docker/keycloak.Dockerfile"
OIDC = "backend/src/property_focus/adapters/auth/oidc.py"
PNPM = "pnpm-workspace.yaml"

_spec = importlib.util.spec_from_file_location("filter_semgrep_sarif", SCRIPT)
assert _spec is not None
assert _spec.loader is not None
filter_semgrep = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(filter_semgrep)


def _result(rule: str, path: str, line: int, message: str = "finding") -> dict[str, Any]:
    return {
        "ruleId": rule,
        "message": {"text": message},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": path},
                    "region": {"startLine": line},
                }
            }
        ],
    }


def test_committed_suppressions_name_only_the_triaged_false_positives() -> None:
    suppressions = filter_semgrep.load_suppressions(SUPPRESSIONS)
    assert {(item.rule, item.path) for item in suppressions} == {
        (SQLALCHEMY, MIGRATION),
        (JWT, SERVER),
        (MISSING_USER, KEYCLOAK),
        (MISSING_ENTRYPOINT, KEYCLOAK),
    }
    for item in suppressions:
        assert item.reason
        assert not item.path.endswith("oidc.py")
        assert item.path != PNPM
        assert "pnpm" not in item.rule


def test_filter_drops_triaged_hits_and_keeps_real_ones() -> None:
    suppressions = filter_semgrep.load_suppressions(SUPPRESSIONS)
    document = {
        "runs": [
            {
                "results": [
                    _result(SQLALCHEMY, MIGRATION, 57),
                    _result(SQLALCHEMY, "backend/src/property_focus/adapters/db/models.py", 150),
                    _result(JWT, SERVER, 168),
                    _result(MISSING_USER, KEYCLOAK, 21),
                    _result(MISSING_ENTRYPOINT, KEYCLOAK, 20),
                    _result(
                        "python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected",
                        OIDC,
                        82,
                        "urlopen scheme",
                    ),
                    _result(
                        "package_managers.pnpm.pnpm-trust-policy.pnpm-trust-policy",
                        PNPM,
                        1,
                        "trustPolicy",
                    ),
                ]
            }
        ]
    }
    updated, result = filter_semgrep.filter_sarif(document, suppressions)
    remaining = {(item.rule, item.path) for item in result.remaining}
    assert remaining == {
        (SQLALCHEMY, "backend/src/property_focus/adapters/db/models.py"),
        (
            "python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected",
            OIDC,
        ),
        ("package_managers.pnpm.pnpm-trust-policy.pnpm-trust-policy", PNPM),
    }
    assert result.unused == ()
    assert len(result.suppressed) == 4
    kept = updated["runs"][0]["results"]
    kept_paths = {
        item["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] for item in kept
    }
    assert kept_paths == {
        "backend/src/property_focus/adapters/db/models.py",
        OIDC,
        PNPM,
    }


def test_main_rewrites_sarif_and_fails_on_a_remaining_finding(tmp_path: Path) -> None:
    sarif = tmp_path / "semgrep.sarif"
    sarif.write_text(
        json.dumps({"runs": [{"results": [_result("rule.kept", "left.py", 1, "still here")]}]}),
        encoding="utf-8",
    )
    suppressions = tmp_path / "suppressions.toml"
    suppressions.write_text(
        '[[suppression]]\nrule = "rule.dropped"\npath = "gone.py"\nreason = "not this file"\n',
        encoding="utf-8",
    )
    code = filter_semgrep.main([str(sarif), "--suppressions", str(suppressions)])
    assert code == 2
    report = json.loads(sarif.read_text(encoding="utf-8"))
    assert report["runs"][0]["results"][0]["ruleId"] == "rule.kept"


def test_main_passes_when_every_finding_is_suppressed(tmp_path: Path) -> None:
    sarif = tmp_path / "semgrep.sarif"
    sarif.write_text(
        json.dumps({"runs": [{"results": [_result("rule.dropped", "gone.py", 4)]}]}),
        encoding="utf-8",
    )
    suppressions = tmp_path / "suppressions.toml"
    suppressions.write_text(
        '[[suppression]]\nrule = "rule.dropped"\npath = "gone.py"\nreason = "triaged"\n',
        encoding="utf-8",
    )
    assert filter_semgrep.main([str(sarif), "--suppressions", str(suppressions)]) == 0
    assert json.loads(sarif.read_text(encoding="utf-8"))["runs"][0]["results"] == []


def test_bandit_skips_are_the_triaged_tests_and_not_the_urlopen_check() -> None:
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert set(config["tool"]["bandit"]["skips"]) == {"B101", "B105", "B404", "B603"}


def test_verify_does_not_depend_on_scan() -> None:
    verify = next(line for line in MAKEFILE.read_text().splitlines() if line.startswith("verify:"))
    assert "scan" not in verify.split()


def test_security_workflow_is_warn_only_and_pinned() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in text
    assert "schedule:" in text
    assert re.search(r"^\s*pull_request_target\s*:", text, re.MULTILINE) is None
    assert 'SCAN_ENFORCE: "false"' in text
    assert text.count("security-events: write") == 1
    assert "make scan-semgrep" in text
    assert "make scan-bandit" in text
    assert "make scan-deps" in text
    uses = re.findall(r"uses: (\S+)", text)
    assert uses
    for use in uses:
        _action, _at, ref = use.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", ref), use
        assert "#" not in use


def test_load_suppressions_rejects_a_blank_reason(tmp_path: Path) -> None:
    path = tmp_path / "suppressions.toml"
    path.write_text(
        '[[suppression]]\nrule = "rule"\npath = "file.py"\nreason = "  "\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="reason"):
        filter_semgrep.load_suppressions(path)
