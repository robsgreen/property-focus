"""The OpenAPI export that the web client is generated from."""

import json
import subprocess
import sys
from pathlib import Path

EXPORT_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_openapi.py"


def export(out: Path) -> str:
    # An empty environment shows the export needs no configuration or live service.
    subprocess.run(
        [sys.executable, EXPORT_SCRIPT, "--out", out], check=True, env={}, capture_output=True
    )
    return out.read_text(encoding="utf-8")


def test_repeated_exports_are_byte_identical(tmp_path: Path) -> None:
    assert export(tmp_path / "first.json") == export(tmp_path / "second.json")


def test_export_is_key_sorted_and_documents_problem_errors(tmp_path: Path) -> None:
    text = export(tmp_path / "openapi.json")
    document = json.loads(text)

    assert text == json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    responses = document["paths"]["/v1/workflow-runs/{run_id}"]["get"]["responses"]
    assert set(responses["404"]["content"]) == {"application/problem+json"}
