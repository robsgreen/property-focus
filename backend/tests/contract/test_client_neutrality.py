"""The REST and MCP adapters treat every client alike: what a client calls itself, in its
``User-Agent`` header or in ``initialize``'s ``clientInfo``, changes no answer, and no
adapter, auth or configuration file names a particular MCP client or reads the user agent.
"""

import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from support.keycloak import REPOSITORY_ROOT, LocalKeycloak, TestUser, Tokens

from contract.mcp_client import McpClient

USER_AGENTS = [
    "Claude-User/1.0",
    "ChatGPT-User/1.0",
    "Cursor/1.7.0",
    "Visual Studio Code/1.104",
    "python-httpx/0.28.1",
    "Mozilla/5.0 (X11; Linux x86_64)",
]
CLIENT_NAMES = ["claude-ai", "chatgpt", "cursor-vscode", "synthetic-client"]
TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
FIXED_CORRELATION_ID = "client-neutrality"

SCANNED_PATHS = [
    REPOSITORY_ROOT / "backend" / "src" / "property_focus" / "adapters",
    REPOSITORY_ROOT / "backend" / "src" / "property_focus" / "config.py",
    REPOSITORY_ROOT / "infrastructure" / "keycloak",
    REPOSITORY_ROOT / "infrastructure" / "dogfood",
    REPOSITORY_ROOT / ".env.example",
    REPOSITORY_ROOT / "compose.yaml",
]
SCANNED_SUFFIXES = {".py", ".json", ".yaml", ".yml", ".example", ".sh", ".crontab", ""}
CLIENT_SPECIFIC = re.compile(
    r"user[-_ ]?agent|\b(claude|chatgpt|copilot|windsurf|vscode|vs code|visual studio"
    r"|mcp-inspector|cursor ide|cursor\.(com|sh))\b",
    re.IGNORECASE,
)


@pytest.fixture
def web_tokens(keycloak: LocalKeycloak, user: TestUser) -> Tokens:
    """``user``'s tokens from the web client."""
    return keycloak.pkce_sign_in(user)


def scanned_files() -> list[Path]:
    files: list[Path] = []
    for path in SCANNED_PATHS:
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        files.extend(
            candidate
            for candidate in candidates
            if candidate.is_file()
            and "__pycache__" not in candidate.parts
            and candidate.suffix in SCANNED_SUFFIXES
        )
    return files


def test_adapters_auth_and_configuration_name_no_client_or_user_agent() -> None:
    files = scanned_files()
    hits = [
        f"{path.relative_to(REPOSITORY_ROOT)}:{number}: {line.strip()}"
        for path in files
        for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1)
        if CLIENT_SPECIFIC.search(line)
    ]

    assert any(path.name == "oidc.py" for path in files)
    assert any(path.name == "realm-property-focus.json" for path in files)
    assert hits == []


def test_challenge_and_metadata_ignore_the_user_agent(
    mcp: TestClient, mcp_client: McpClient
) -> None:
    answers = []
    for user_agent in USER_AGENTS:
        challenge = mcp_client.post(TOOLS_LIST, **{"User-Agent": user_agent})
        metadata = mcp.get(
            "/.well-known/oauth-protected-resource", headers={"User-Agent": user_agent}
        )
        answers.append(
            (
                challenge.status_code,
                challenge.headers["www-authenticate"],
                metadata.status_code,
                metadata.json(),
            )
        )

    assert answers[0][0] == 401
    assert all(answer == answers[0] for answer in answers)


def test_mcp_answers_ignore_the_user_agent_and_client_info(
    mcp_client: McpClient, web_tokens: Tokens
) -> None:
    token = web_tokens.access_token

    def answers(user_agent: str, client_name: str) -> tuple[Any, ...]:
        initialized = mcp_client.post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": client_name, "version": "1.0.0"},
                },
            },
            token=token,
            **{"User-Agent": user_agent},
        )
        listed = mcp_client.post(
            TOOLS_LIST,
            token=token,
            **{"User-Agent": user_agent, "MCP-Protocol-Version": "2025-06-18"},
        )
        return (initialized.status_code, initialized.json(), listed.status_code, listed.json())

    results = [
        answers(user_agent, client_name)
        for user_agent in USER_AGENTS
        for client_name in CLIENT_NAMES
    ]

    assert results[0][0] == results[0][2] == 200
    assert all(result == results[0] for result in results)


def test_rest_answers_ignore_the_user_agent(api: TestClient, web_tokens: Tokens) -> None:
    run_id = uuid.uuid4()

    def answer(user_agent: str) -> tuple[int, Any]:
        response = api.get(
            f"/v1/workflow-runs/{run_id}",
            headers={
                "Authorization": f"Bearer {web_tokens.access_token}",
                "User-Agent": user_agent,
                "X-Correlation-ID": FIXED_CORRELATION_ID,
            },
        )
        return response.status_code, response.json()

    results = [answer(user_agent) for user_agent in USER_AGENTS]

    assert results[0][0] == 404
    assert all(result == results[0] for result in results)
