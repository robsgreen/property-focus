"""Enforce the import boundaries between backend layers.

Rules (D18 modular monolith):

- ``property_focus.domain`` stays pure: it must not import ``property_focus.adapters``,
  ``property_focus.runtime``, FastAPI, boto3 or Playwright.
- ``property_focus.application`` may import only the database and telemetry adapters
  (``property_focus.adapters.db``, ``property_focus.adapters.telemetry``): no delivery
  adapter (REST, MCP), no auth adapter and no other adapter.

Every ``import`` and ``from ... import`` statement is checked, including relative imports
and imports under ``if TYPE_CHECKING:``. A layer directory that does not exist yet has
nothing to check.

Usage: ``python backend/scripts/check_boundaries.py [--src DIR]``. Prints one line per
violation and exits 1 when any exist, otherwise exits 0.
"""

import argparse
import ast
import sys
from collections.abc import Iterator
from pathlib import Path

PACKAGE = "property_focus"
DEFAULT_SRC = Path(__file__).resolve().parents[1] / "src"

# Layer package -> module prefixes it must not import. A prefix matches the module itself
# and its submodules (``boto3`` matches ``boto3.session`` but not ``boto3x``).
FORBIDDEN_IMPORTS: dict[str, tuple[str, ...]] = {
    f"{PACKAGE}.domain": (
        f"{PACKAGE}.adapters",
        f"{PACKAGE}.runtime",
        "fastapi",
        "boto3",
        "playwright",
    ),
    f"{PACKAGE}.application": (f"{PACKAGE}.adapters",),
}
# Layer package -> module prefixes it may import although they fall under a forbidden one.
ALLOWED_IMPORTS: dict[str, tuple[str, ...]] = {
    f"{PACKAGE}.application": (f"{PACKAGE}.adapters.db", f"{PACKAGE}.adapters.telemetry"),
}


def find_violations(src: Path) -> list[str]:
    """Return a ``path:line: message`` entry for each forbidden import under ``src``.

    ``src`` is the directory that contains the ``property_focus`` package.
    """
    violations = []
    for layer, forbidden in FORBIDDEN_IMPORTS.items():
        allowed = ALLOWED_IMPORTS.get(layer, ())
        layer_dir = src.joinpath(*layer.split("."))
        for path in sorted(layer_dir.rglob("*.py")):
            module = _module_name(src, path)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for line, imported in _imported_modules(tree, module, path.name == "__init__.py"):
                if any(_is_within(imported, prefix) for prefix in forbidden) and not any(
                    _is_within(imported, prefix) for prefix in allowed
                ):
                    violations.append(f"{path}:{line}: {layer} must not import {imported}")
    return violations


def _module_name(src: Path, path: Path) -> str:
    parts = path.relative_to(src).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imported_modules(tree: ast.Module, module: str, is_package: bool) -> Iterator[tuple[int, str]]:
    """Yield ``(line, absolute module name)`` for every module an import statement can bind.

    ``from pkg import name`` yields both ``pkg`` and ``pkg.name``, because ``name`` may be a
    submodule (``from property_focus import adapters``).
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(node, module, is_package)
            yield node.lineno, base
            for alias in node.names:
                if alias.name != "*":
                    yield node.lineno, f"{base}.{alias.name}" if base else alias.name


def _resolve_from(node: ast.ImportFrom, module: str, is_package: bool) -> str:
    """Return the absolute module a ``from`` import reads from, resolving leading dots."""
    if node.level == 0:
        return node.module or ""
    package_parts = module.split(".") if is_package else module.split(".")[:-1]
    anchor = package_parts[: len(package_parts) - (node.level - 1)]
    return ".".join([*anchor, node.module] if node.module else anchor)


def _is_within(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(prefix + ".")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--src",
        type=Path,
        default=DEFAULT_SRC,
        help="directory containing the property_focus package (default: backend/src)",
    )
    args = parser.parse_args(argv)

    violations = find_violations(args.src)
    for violation in violations:
        print(violation, file=sys.stderr)
    if violations:
        print(f"check_boundaries: {len(violations)} forbidden import(s)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
