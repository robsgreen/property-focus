"""Write the REST API's OpenAPI document, the input of the web client generator.

The document is built from the FastAPI application without starting it, so no database,
identity provider or other live service is needed. The application is configured with fixed
development settings rather than the process environment, so the output depends only on the
code. Keys are sorted and the JSON is indented the same way on every run, so regenerating an
unchanged API rewrites the file byte for byte.

Usage: ``python backend/scripts/export_openapi.py --out frontend/src/api/openapi.json``.
"""

import argparse
import json
from pathlib import Path
from typing import Any

from property_focus.adapters.rest.app import create_app
from property_focus.config import Settings


def openapi_document() -> dict[str, Any]:
    """The application's OpenAPI document, as FastAPI publishes it at ``/openapi.json``."""
    app = create_app(Settings.model_validate({"environment": "development"}))
    return app.openapi()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="file to write the document to")
    args = parser.parse_args()
    text = json.dumps(openapi_document(), indent=2, sort_keys=True, ensure_ascii=False)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
