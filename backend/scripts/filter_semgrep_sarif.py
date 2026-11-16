"""Drop triaged Semgrep false positives from a SARIF file and report the rest.

``make scan-semgrep`` writes Semgrep's SARIF, then runs this script on it. A
suppression removes every result of one rule in one file. Results that remain
are printed and kept in the file, which CI uploads for code scanning.

The suppression list is ``semgrep_suppressions.toml`` next to this script.
``adapters/auth/oidc.py`` and ``pnpm-workspace.yaml`` are not in it.

Usage: ``python backend/scripts/filter_semgrep_sarif.py SARIF [--suppressions FILE]``.
Exit codes: 0 when nothing remains, 1 when a finding remains, 2 when the SARIF
or a suppression entry is unusable or a suppression matched nothing.
"""

import argparse
import copy
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_SUPPRESSIONS = Path(__file__).resolve().parent / "semgrep_suppressions.toml"


@dataclass(frozen=True)
class Suppression:
    """One rule on one path, with the reason it is a false positive."""

    rule: str
    path: str
    reason: str


@dataclass(frozen=True)
class Finding:
    """One SARIF result, reduced to the fields the report prints."""

    rule: str
    path: str
    line: int
    message: str


@dataclass(frozen=True)
class FilterResult:
    """What remained, what was dropped, and which suppressions matched nothing."""

    remaining: tuple[Finding, ...]
    suppressed: tuple[tuple[Finding, Suppression], ...]
    unused: tuple[Suppression, ...]


def load_suppressions(path: Path) -> tuple[Suppression, ...]:
    """Read ``path``. Raises ``ValueError`` when an entry is empty or repeated."""
    document = tomllib.loads(path.read_text(encoding="utf-8"))
    entries = document.get("suppression", [])
    if not isinstance(entries, list):
        raise ValueError("suppression must be a list of tables")
    suppressions: list[Suppression] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"suppression {index} is not a table")
        rule = _required_text(entry, "rule")
        file_path = _required_text(entry, "path")
        reason = _required_text(entry, "reason")
        if rule is None or file_path is None or reason is None:
            raise ValueError(f"suppression {index} needs rule, path and reason")
        key = (rule, file_path)
        if key in seen:
            raise ValueError(f"suppression {index} repeats {rule} in {file_path}")
        seen.add(key)
        suppressions.append(Suppression(rule, file_path, reason.strip()))
    return tuple(suppressions)


def filter_sarif(
    document: dict[str, Any], suppressions: tuple[Suppression, ...]
) -> tuple[dict[str, Any], FilterResult]:
    """Return a copy of ``document`` without suppressed results, and the report.

    A result with no path is kept. A suppression that matches nothing is unused.
    """
    updated = copy.deepcopy(document)
    remaining: list[Finding] = []
    suppressed: list[tuple[Finding, Suppression]] = []
    runs = updated.get("runs")
    if not isinstance(runs, list):
        raise ValueError("SARIF has no runs list")
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("SARIF run is not an object")
        results = run.get("results", [])
        if not isinstance(results, list):
            raise ValueError("SARIF results is not a list")
        kept: list[dict[str, Any]] = []
        for result in results:
            if not isinstance(result, dict):
                raise ValueError("SARIF result is not an object")
            finding = _finding(result)
            match = _match(finding, suppressions)
            if match is None:
                kept.append(result)
                remaining.append(finding)
            else:
                suppressed.append((finding, match))
        run["results"] = kept
    used = {(item.rule, item.path) for _, item in suppressed}
    unused = tuple(item for item in suppressions if (item.rule, item.path) not in used)
    return updated, FilterResult(tuple(remaining), tuple(suppressed), unused)


def render_report(result: FilterResult) -> str:
    """The human-readable report ``main`` prints."""
    lines = [
        f"Semgrep: {len(result.remaining)} finding(s) after suppressions, "
        f"{len(result.suppressed)} suppressed."
    ]
    for finding in result.remaining:
        lines.append(f"remaining  {finding.rule}  {finding.path}:{finding.line}")
        lines.append(f"  {finding.message}")
    for finding, suppression in result.suppressed:
        lines.append(f"suppressed {finding.rule}  {finding.path}:{finding.line}")
        lines.append(f"  {suppression.reason}")
    for suppression in result.unused:
        lines.append(f"unused suppression {suppression.rule} {suppression.path}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Filter ``argv``'s SARIF file in place. See the module docstring for exit codes."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sarif", type=Path, help="SARIF file to filter in place")
    parser.add_argument(
        "--suppressions",
        type=Path,
        default=DEFAULT_SUPPRESSIONS,
        help="TOML suppression list (default: next to this script)",
    )
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.sarif.read_text(encoding="utf-8"))
        suppressions = load_suppressions(args.suppressions)
        if not isinstance(document, dict):
            raise ValueError("SARIF root is not an object")
        updated, result = filter_sarif(document, suppressions)
    except (OSError, json.JSONDecodeError, tomllib.TOMLDecodeError, ValueError) as error:
        print(f"filter-semgrep-sarif: {error}")
        return 2
    args.sarif.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
    print(render_report(result))
    if result.unused:
        return 2
    if result.remaining:
        return 1
    return 0


def _required_text(entry: dict[str, Any], key: str) -> str | None:
    """A non-blank string field of a suppression table, or None."""
    value = entry.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _finding(result: dict[str, Any]) -> Finding:
    """The rule, path, line and first message line of one SARIF result."""
    rule = result.get("ruleId")
    rule_id = rule if isinstance(rule, str) else ""
    message = result.get("message")
    text = message.get("text") if isinstance(message, dict) else ""
    summary = text.splitlines()[0] if isinstance(text, str) and text else ""
    located = _location(result)
    if located is None:
        return Finding(rule_id, "", 0, summary)
    return Finding(rule_id, located[0], located[1], summary)


def _location(result: dict[str, Any]) -> tuple[str, int] | None:
    """The first result location's repo-relative path and start line, if it has one."""
    locations = result.get("locations")
    if not isinstance(locations, list) or not locations or not isinstance(locations[0], dict):
        return None
    physical = locations[0].get("physicalLocation")
    if not isinstance(physical, dict):
        return None
    artifact = physical.get("artifactLocation")
    if not isinstance(artifact, dict):
        return None
    uri = artifact.get("uri")
    if not isinstance(uri, str) or not uri:
        return None
    if uri.startswith("./"):
        uri = uri[2:]
    region = physical.get("region")
    line = region.get("startLine") if isinstance(region, dict) else 0
    return uri, line if isinstance(line, int) else 0


def _match(finding: Finding, suppressions: tuple[Suppression, ...]) -> Suppression | None:
    """The suppression for this finding, or None when it should be reported."""
    if not finding.path:
        return None
    return next(
        (item for item in suppressions if item.rule == finding.rule and item.path == finding.path),
        None,
    )


if __name__ == "__main__":
    raise SystemExit(main())
