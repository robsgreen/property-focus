"""The token and MCP protocol code rests on maintained libraries, pinned to ranges that
exclude releases with known advisories and the next incompatible line, and the committed
lockfile resolves inside those ranges.

PyJWT below 2.15.1 shares decode options between calls (GHSA-gvp8-978c-rx2q); the MCP
adapter is written against the ``mcp`` 2.3 API, so 2.4 must not arrive by a plain lock
refresh. ``pytest`` brings ``packaging``, which evaluates the ranges as installers do.
"""

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_MANIFEST = REPO_ROOT / "backend" / "pyproject.toml"
LOCKFILE = REPO_ROOT / "uv.lock"

# Per library: versions the range must admit, and versions it must refuse.
EXPECTED_RANGES = {
    "pyjwt": (["2.15.1", "2.99.0"], ["2.15.0", "2.10.1", "3.0.0"]),
    "mcp": (["2.3.0", "2.3.9"], ["2.2.9", "2.4.0", "3.0.0"]),
}


def backend_requirements() -> dict[str, Requirement]:
    manifest = tomllib.loads(BACKEND_MANIFEST.read_text())
    requirements = (Requirement(line) for line in manifest["project"]["dependencies"])
    return {requirement.name.lower(): requirement for requirement in requirements}


def locked_version(package: str) -> Version:
    lock = tomllib.loads(LOCKFILE.read_text())
    (entry,) = [item for item in lock["package"] if item["name"] == package]
    return Version(entry["version"])


@pytest.mark.parametrize("package", EXPECTED_RANGES)
def test_backend_pins_a_bounded_range_that_excludes_vulnerable_and_incompatible_releases(
    package: str,
) -> None:
    admitted, refused = EXPECTED_RANGES[package]
    specifier = backend_requirements()[package].specifier

    assert [version for version in admitted if version not in specifier] == []
    assert [version for version in refused if version in specifier] == []


def test_pyjwt_is_installed_with_its_crypto_extra() -> None:
    assert "crypto" in backend_requirements()["pyjwt"].extras


@pytest.mark.parametrize("package", EXPECTED_RANGES)
def test_lockfile_resolves_inside_the_pinned_range(package: str) -> None:
    specifier = backend_requirements()[package].specifier

    assert locked_version(package) in specifier
