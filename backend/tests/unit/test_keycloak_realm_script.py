"""The pure parts of ``backend/scripts/keycloak_realm.py``: filling the realm file's
placeholders and comparing a running realm with the file. The admin API calls are covered
against the local Keycloak in ``tests/integration/test_keycloak_realm.py``."""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "keycloak_realm.py"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("keycloak_realm", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


realm_script = load_script()


def write_realm(tmp_path: Path, realm: object) -> Path:
    path = tmp_path / "realm.json"
    path.write_text(json.dumps(realm))
    return path


def test_fills_environment_placeholders_and_defaults_but_not_message_keys(
    tmp_path: Path,
) -> None:
    path = write_realm(
        tmp_path,
        {
            "smtpServer": {"host": "${PF_HOST}", "auth": "${PF_AUTH:false}", "user": "${PF_U:}"},
            "redirectUris": ["${PF_ORIGIN}/auth/callback"],
            "consent": "${profileScopeConsentText}",
        },
    )

    realm = realm_script.load_realm(
        path, {"PF_HOST": 'mail "relay"', "PF_ORIGIN": "https://app.example.test"}
    )

    assert realm == {
        "smtpServer": {"host": 'mail "relay"', "auth": "false", "user": ""},
        "redirectUris": ["https://app.example.test/auth/callback"],
        "consent": "${profileScopeConsentText}",
    }


def test_names_every_placeholder_without_value_or_default(tmp_path: Path) -> None:
    path = write_realm(tmp_path, {"a": "${PF_B}", "b": ["${PF_A}", "${PF_C:set}"]})

    with pytest.raises(realm_script.MissingVariableError, match="PF_A, PF_B"):
        realm_script.load_realm(path, {})


@pytest.mark.parametrize(
    ("desired", "actual", "expected"),
    [
        pytest.param({"a": 1}, {"a": 1, "server_default": 2}, [], id="extra-keys-ignored"),
        pytest.param({"a": {"b": "x"}}, {"a": {"b": "y"}}, ["a.b"], id="nested-value"),
        pytest.param({"a": ["x", "y"]}, {"a": ["y", "x"]}, [], id="plain-list-as-set"),
        pytest.param({"a": ["x"]}, {"a": ["x", "z"]}, ["a"], id="plain-list-extra-item"),
        pytest.param(
            {"a": [{"n": 1}, {"n": 2}]},
            {"a": [{"n": 2}, {"n": 1}]},
            ["a[0].n", "a[1].n"],
            id="object-list-in-order",
        ),
        pytest.param({"a": [{"n": 1}]}, {"a": []}, ["a"], id="object-list-length"),
        pytest.param({"password": "new"}, {"password": "**********"}, [], id="masked-secret"),
        pytest.param({"user": ""}, {}, [], id="empty-value-omitted-by-server"),
        pytest.param({"user": "x"}, {}, ["user"], id="missing-key"),
    ],
)
def test_differences_compare_only_what_the_file_names(
    desired: object, actual: object, expected: list[str]
) -> None:
    assert realm_script.differences(desired, actual) == expected


def test_merged_lays_the_file_over_the_server_representation() -> None:
    actual = {"id": "1", "attributes": {"kept": "yes", "changed": "old"}, "list": [1, 2]}
    desired = {"attributes": {"changed": "new"}, "list": [3]}

    assert realm_script.merged(actual, desired) == {
        "id": "1",
        "attributes": {"kept": "yes", "changed": "new"},
        "list": [3],
    }
