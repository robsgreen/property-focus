"""Fail when a committed file or a fixture contains something that looks like a credential.

Scanned files:

- every file Git tracks or would add (tracked, plus untracked files not ignored);
- every file under ``fixtures/``, including ignored ones, because recorded email, pages
  and evidence fixtures are the likeliest place for a real secret to slip in.

Detected credential patterns: cloud and API keys, access and bearer tokens, JSON Web
Tokens, cookies and browser session state, and private keys. Binary files are skipped.
A hit reports the file, line and pattern name but never the matched value, so the check
itself cannot leak a secret into CI logs.

Usage: ``python backend/scripts/check_fixture_secrets.py [--root DIR]``. Exits 1 when any
file matches, 2 when ``DIR`` is not a Git work tree, otherwise 0.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = "fixtures"

# Values made of these words are documentation placeholders, not credentials.
_PLACEHOLDER = re.compile(r"example|placeholder|changeme|dummy|redacted|your[-_]|xxxx|\.\.\.", re.I)
# A whole value of the form ${NAME} or ${NAME:} is an environment variable reference that the
# deployment fills in (a Keycloak realm file, for example). A non-empty default after the
# colon would itself be a literal value, so it does not count.
_ENVIRONMENT_REFERENCE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*:?\}")

# Pattern name -> regex. Group 1, when present, is the candidate value checked against
# _PLACEHOLDER; patterns without a group are specific enough to report on sight.
CREDENTIAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "private key": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"),
    "AWS access key ID": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "AWS secret access key": re.compile(
        r"(?i)aws_secret_access_key[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9/+=]{40})\b"
    ),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})"),
    "AI provider API key": re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "Slack token": re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    "JSON Web Token": re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    ),
    "bearer token": re.compile(
        r"(?i)\bauthorization[\"']?\s*[:=]\s*[\"']?bearer\s+([A-Za-z0-9._~+/=-]{20,})"
    ),
    "cookie header": re.compile(r"(?im)^\s*(?:set-)?cookie:\s*[^=\s;]+=([^;\s]{16,})"),
    "browser session state": re.compile(r"\"cookies\"\s*:\s*\[\s*\{"),
    "credential assignment": re.compile(
        r"(?i)\b[\w-]*(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
        r"[\"']?\s*[:=]\s*[\"']([^\"'\s]{16,})[\"']"
    ),
}


class NotAGitWorkTreeError(Exception):
    """The scan root is not inside a Git work tree, so committed files cannot be listed."""


def scan(root: Path) -> list[str]:
    """Return a ``path:line: pattern name`` entry for each credential found under ``root``.

    Raises ``NotAGitWorkTreeError`` when ``root`` is not inside a Git work tree.
    """
    findings = []
    for path in _files_to_scan(root):
        text = _read_text(root / path)
        if text is None:
            continue
        for name, pattern in CREDENTIAL_PATTERNS.items():
            for match in pattern.finditer(text):
                if pattern.groups and _is_placeholder(match.group(1)):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{path}:{line}: {name}")
    return findings


def _is_placeholder(value: str) -> bool:
    """True when a matched credential value is a documentation placeholder or an env reference."""
    return bool(_PLACEHOLDER.search(value) or _ENVIRONMENT_REFERENCE.fullmatch(value))


def _files_to_scan(root: Path) -> list[Path]:
    """Return repository-relative paths: Git-visible files plus everything under fixtures."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise NotAGitWorkTreeError(result.stderr.decode(errors="replace").strip())
    paths = {Path(name) for name in result.stdout.decode().split("\0") if name}
    paths.update(p.relative_to(root) for p in (root / FIXTURES_DIR).rglob("*") if p.is_file())
    return sorted(paths)


def _read_text(path: Path) -> str | None:
    """Return the file's text, or None when it is missing (deleted, not yet committed) or binary."""
    try:
        data = path.read_bytes()
    except (FileNotFoundError, IsADirectoryError):
        return None
    if b"\0" in data:
        return None
    return data.decode("utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--root",
        type=Path,
        default=DEFAULT_ROOT,
        help="repository root to scan (default: this repository)",
    )
    args = parser.parse_args(argv)

    try:
        findings = scan(args.root)
    except NotAGitWorkTreeError as error:
        print(
            f"check_fixture_secrets: {args.root} is not a Git work tree: {error}", file=sys.stderr
        )
        return 2
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        print(
            f"check_fixture_secrets: {len(findings)} possible credential(s); remove them or "
            "replace them with synthetic placeholder values",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
