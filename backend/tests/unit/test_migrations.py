"""Migration inputs that must not change once a migration has run anywhere."""

import hashlib
import importlib.util
import itertools
from importlib.resources import files
from pathlib import Path
from types import ModuleType

import pytest

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"

# Migration 0001 executes ``rls.sql`` as it is when the migration runs, so an edit to the file
# would silently rewrite what that migration installs on a fresh database while leaving every
# migrated one as it was. The file belongs to migration 0001: change roles, privileges or
# policies in a new migration, never here. This digest pins the file's bytes.
RLS_SQL_SHA256 = "5efb7db6817931a89805034c40f90c7cf8a735b0f18bcdbc93182a40c85c24c1"


def test_the_row_level_security_script_of_migration_0001_is_unchanged() -> None:
    script = files("property_focus.adapters.db").joinpath("rls.sql").read_bytes()

    assert hashlib.sha256(script).hexdigest() == RLS_SQL_SHA256


def _migrations() -> list[ModuleType]:
    modules = []
    for path in sorted(VERSIONS.glob("[0-9]*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    return modules


def test_migrations_form_one_appended_chain() -> None:
    revisions = [(module.revision, module.down_revision) for module in _migrations()]

    assert revisions[0] == ("0001", None)
    for (previous, _), (_, down) in itertools.pairwise(revisions):
        assert down == previous


@pytest.mark.parametrize("script", ["UPGRADE_SQL", "DOWNGRADE_SQL"])
def test_scripts_sent_through_the_driver_have_no_percent_signs(script: str) -> None:
    # The driver reads a percent sign as a parameter placeholder.
    for module in _migrations():
        assert "%" not in getattr(module, script, ""), module.__name__
