"""Migration inputs that must not change once a migration has run anywhere."""

import hashlib
from importlib.resources import files

# Migration 0001 executes ``rls.sql`` as it is when the migration runs, so an edit to the file
# would silently rewrite what that migration installs on a fresh database while leaving every
# migrated one as it was. The file belongs to migration 0001: change roles, privileges or
# policies in a new migration, never here. This digest pins the file's bytes.
RLS_SQL_SHA256 = "5efb7db6817931a89805034c40f90c7cf8a735b0f18bcdbc93182a40c85c24c1"


def test_the_row_level_security_script_of_migration_0001_is_unchanged() -> None:
    script = files("property_focus.adapters.db").joinpath("rls.sql").read_bytes()

    assert hashlib.sha256(script).hexdigest() == RLS_SQL_SHA256
