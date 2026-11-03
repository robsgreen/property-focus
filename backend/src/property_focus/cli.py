"""Command-line entry point: ``property-focus <command>``, or ``python -m property_focus.cli``.

Commands:

- ``migrate``: upgrade the database to the latest schema as the migration owner.
- ``seed --dataset synthetic``: create the synthetic users in
  ``fixtures/synthetic/accounts.json`` and their accounts; existing ones are left as they are.
- ``run-task <kind>``: run the scheduled kind once (see ``runtime/scheduled.py``).
- ``run-task <kind> --task-input <json>``: run one task for a workflow run, as the dogfood
  launcher does; the JSON is a ``TaskInput`` whose ``task_kind`` is ``<kind>``.
- ``serve {api,mcp} [--host HOST] [--port PORT]``: serve the REST API (default port 8000)
  or the MCP adapter (default port 8001) with uvicorn, on 127.0.0.1 unless ``--host`` says
  otherwise. Behind a proxy, uvicorn's ``FORWARDED_ALLOW_IPS`` names the proxies whose
  ``X-Forwarded-Proto`` and ``X-Forwarded-For`` headers it trusts.
- ``schedule --crontab <file>``: run the scheduler daemon over a crontab until stopped by
  SIGTERM or SIGINT (see ``runtime/scheduler.py``).

Configuration comes from the ``PF_*`` environment (see ``config.py``). Exit codes: 0 on
success (a task's own failure is recorded on its workflow run), 2 for invalid
configuration or usage.
"""

import argparse
import asyncio
import json
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

import uvicorn
from pydantic import ValidationError

from property_focus.adapters.db import connect_runtime, migrate
from property_focus.adapters.mcp.server import create_app as create_mcp_app
from property_focus.adapters.rest.app import create_app as create_rest_app
from property_focus.adapters.telemetry import configure_telemetry, flush_telemetry
from property_focus.application.context import VerifiedIdentity, provision_tenant
from property_focus.application.ports import TaskInput
from property_focus.config import ConfigurationError, Settings
from property_focus.runtime.run_task import (
    DEFAULT_TASKS,
    TaskRegistry,
    run_task,
    runtime_from_settings,
)
from property_focus.runtime.scheduled import SCHEDULED_KINDS, run_scheduled
from property_focus.runtime.scheduler import CrontabError, Scheduler, parse_crontab

# The repository's fixtures directory, found relative to this module in the source tree.
FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"
# Seed dataset name -> its accounts file under FIXTURES_DIR.
SEED_DATASETS = {"synthetic": "synthetic/accounts.json"}
# Servable application -> its default port.
SERVER_PORTS = {"api": 8000, "mcp": 8001}


def main(argv: Sequence[str] | None = None, *, tasks: TaskRegistry = DEFAULT_TASKS) -> int:
    """Run the command named in ``argv`` (default: ``sys.argv[1:]``); return the exit code.

    ``tasks`` is the registry ``run-task`` resolves task kinds in.
    """
    parser = argparse.ArgumentParser(prog="property-focus")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="upgrade the database to the latest schema")
    seed_parser = commands.add_parser("seed", help="create a fixture dataset's accounts")
    seed_parser.add_argument("--dataset", required=True, choices=sorted(SEED_DATASETS))
    run_task_parser = commands.add_parser("run-task", help="run one task or scheduled kind")
    run_task_parser.add_argument("kind", help="the task or scheduled kind")
    run_task_parser.add_argument("--task-input", help="the TaskInput JSON of a workflow task")
    serve_parser = commands.add_parser("serve", help="serve the REST API or the MCP adapter")
    serve_parser.add_argument("application", choices=sorted(SERVER_PORTS))
    serve_parser.add_argument("--host", default="127.0.0.1", help="address to listen on")
    serve_parser.add_argument("--port", type=int, help="port to listen on")
    schedule_parser = commands.add_parser("schedule", help="run the scheduler daemon")
    schedule_parser.add_argument("--crontab", type=Path, required=True, help="crontab file")
    arguments = parser.parse_args(argv)

    try:
        settings = Settings.from_environment()
    except ConfigurationError as error:
        print(f"property-focus: {error}", file=sys.stderr)
        return 2

    if arguments.command == "migrate":
        migrate(settings)
        return 0
    if arguments.command == "seed":
        return asyncio.run(_seed(settings, FIXTURES_DIR / SEED_DATASETS[arguments.dataset]))
    if arguments.command == "serve":
        port = arguments.port or SERVER_PORTS[arguments.application]
        return _serve(settings, arguments.application, arguments.host, port)
    if arguments.command == "schedule":
        return _schedule(settings, arguments.crontab)
    return asyncio.run(_run_task(settings, arguments.kind, arguments.task_input, tasks))


def _serve(settings: Settings, application: str, host: str, port: int) -> int:
    """Serve ``application`` until uvicorn stops (on SIGTERM or SIGINT)."""
    configure_telemetry(settings, f"property-focus-{application}")
    app = create_rest_app(settings) if application == "api" else create_mcp_app(settings)
    # log_config=None keeps the JSON log handler telemetry installed on the root logger. The
    # access log is off: it records query strings, and the REST application logs each request
    # without one.
    uvicorn.run(app, host=host, port=port, proxy_headers=True, log_config=None, access_log=False)
    return 0


def _schedule(settings: Settings, crontab: Path) -> int:
    """Run the scheduler over ``crontab`` until SIGTERM or SIGINT."""
    try:
        entries = parse_crontab(crontab.read_text(encoding="utf-8"))
    except (OSError, CrontabError) as error:
        print(f"property-focus: {crontab}: {error}", file=sys.stderr)
        return 2
    configure_telemetry(settings, "property-focus-scheduler")
    stop = threading.Event()
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(stop_signal, lambda *_: stop.set())
    Scheduler(entries).run(stop)
    return 0


async def _seed(settings: Settings, accounts_file: Path) -> int:
    """Provision every account in ``accounts_file`` as the runtime role, in one transaction."""
    identities = [
        VerifiedIdentity(
            issuer=account["issuer"],
            subject=account["subject"],
            email=account["email"],
            email_verified=account["email_verified"],
        )
        for account in json.loads(accounts_file.read_text())["accounts"]
    ]
    engine = await connect_runtime(settings, pool_size=1)
    try:
        with engine.begin() as connection:
            for identity in identities:
                provision_tenant(connection, identity)
    finally:
        engine.dispose()
    print(f"seed: {len(identities)} accounts present")
    return 0


async def _run_task(
    settings: Settings, kind: str, task_input: str | None, tasks: TaskRegistry
) -> int:
    if task_input is None:
        if kind not in SCHEDULED_KINDS:
            print(f"property-focus: {kind!r} is not a scheduled kind", file=sys.stderr)
            return 2
        task = None
    else:
        try:
            task = TaskInput.model_validate_json(task_input)
        except ValidationError as error:
            print(f"property-focus: invalid --task-input: {error}", file=sys.stderr)
            return 2
        if task.task_kind != kind:
            print(f"property-focus: --task-input is for {task.task_kind!r}", file=sys.stderr)
            return 2

    configure_telemetry(settings, "property-focus-task")
    runtime = await runtime_from_settings(settings, tasks)
    try:
        if task is None:
            handled = await run_scheduled(kind, runtime)
            print(f"{kind}: {handled} handled")
        else:
            print(f"{kind} {task.workflow_run_id}: {await run_task(task, runtime)}")
    finally:
        runtime.engine.dispose()
        flush_telemetry()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
