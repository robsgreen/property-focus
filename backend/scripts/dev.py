"""Run the local development stack: what ``make dev`` starts.

- the REST API on 127.0.0.1:8000 and the MCP adapter on 127.0.0.1:8001, as host processes
  served from the source tree;
- the Vite development server for the web app on localhost:5173;
- the scheduler daemon over ``infrastructure/dogfood/scheduler.crontab``, the same entries
  dogfood runs.

All four use the process environment (``make dev`` loads ``.env``); the identity provider is
the local Keycloak realm unless ``PF_OIDC_ISSUER`` says otherwise. The command refuses any
configuration that is not ``PF_ENVIRONMENT=development`` and exits 2 without starting
anything. Otherwise it runs until one of the processes exits or it receives SIGINT or
SIGTERM, then stops the rest and exits with the first non-zero status, or 0.

Usage: ``uv run --package property-focus python backend/scripts/dev.py``. The local services
(``docker compose up -d postgres keycloak mailpit otel``) and a migrated database come first.
"""

import signal
import subprocess
import sys
import threading
from pathlib import Path

from property_focus.config import ConfigurationError, DevelopmentOnlyError, Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CRONTAB = REPOSITORY_ROOT / "infrastructure" / "dogfood" / "scheduler.crontab"
_CLI = (sys.executable, "-m", "property_focus.cli")

# Name -> command line of each process the stack runs, from the repository root.
PROCESSES: dict[str, tuple[str, ...]] = {
    "api": (*_CLI, "serve", "api", "--host", "127.0.0.1", "--port", "8000"),
    "mcp": (*_CLI, "serve", "mcp", "--host", "127.0.0.1", "--port", "8001"),
    "web": ("pnpm", "--filter", "@property-focus/web", "dev"),
    "scheduler": (*_CLI, "schedule", "--crontab", str(CRONTAB)),
}

# How long a process may take to exit after SIGTERM before it is killed.
_STOP_TIMEOUT_SECONDS = 10


def main() -> int:
    try:
        Settings.from_environment().require_development("make dev")
    except (ConfigurationError, DevelopmentOnlyError) as error:
        print(f"make dev: {error}", file=sys.stderr)
        return 2

    stop = threading.Event()
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(stop_signal, lambda *_: stop.set())

    running = {
        name: subprocess.Popen(command, cwd=REPOSITORY_ROOT, stdin=subprocess.DEVNULL)
        for name, command in PROCESSES.items()
    }
    status = 0
    while not stop.wait(0.5):
        exited = [
            (name, code)
            for name, process in running.items()
            if (code := process.poll()) is not None
        ]
        if exited:
            name, status = exited[0]
            print(f"make dev: {name} exited with status {status}; stopping", file=sys.stderr)
            break

    for process in running.values():
        if process.poll() is None:
            process.terminate()
    for process in running.values():
        try:
            process.wait(timeout=_STOP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
    return status


if __name__ == "__main__":
    raise SystemExit(main())
