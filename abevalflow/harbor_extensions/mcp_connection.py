"""Attach a trusted managed MCP endpoint to native Harbor task packages.

One fresh PipelineRun per case/condition is required until per-case reset exists.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import tomlkit

LABEL_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}\Z")


def managed_connection(target_file, uid, run_name):
    if not uid or not run_name or not LABEL_VALUE.fullmatch(uid) or not LABEL_VALUE.fullmatch(run_name):
        raise ValueError("managed MCP requires a valid run UID and name")
    target = json.loads(Path(target_file).read_text())
    if target.get("uid") != uid:
        raise ValueError("stale managed MCP target")
    url = urlparse(target.get("endpoint", ""))
    if url.scheme != "http" or url.username or url.password or url.query or url.fragment:
        raise ValueError("managed MCP requires a plain internal HTTP endpoint")
    if not url.hostname or not url.hostname.startswith(target["name"] + ".") or not url.hostname.endswith(".svc"):
        raise ValueError("managed MCP endpoint does not match its service identity")
    return {"name": "managed-mock", "transport": "streamable-http", "url": target["endpoint"]}


def configure_task(task_dir, connection):
    path = Path(task_dir) / "task.toml"
    doc = tomlkit.parse(path.read_text())
    env = doc.setdefault("environment", tomlkit.table())
    existing = env.get("mcp_servers", [])
    if existing and [dict(item) for item in existing] != [connection]:
        raise ValueError("managed mock conflicts with existing MCP connections")
    env["mcp_servers"] = [connection]
    path.write_text(tomlkit.dumps(doc))


def configure_job(config, task_dir, target_file, uid, run_name):
    """Wire native MCP and isolate its child Pod with the existing policy."""
    if config.get("n_attempts", 1) != 1 or config.get("n_concurrent_trials", 1) != 1:
        raise ValueError("managed Harbor mock currently requires one case/attempt per PipelineRun")
    tasks = config.get("tasks", [])
    if len(tasks) != 1 or Path(tasks[0]["path"]).resolve() != Path(task_dir).resolve():
        raise ValueError("managed Harbor mock requires exactly one task directory")
    if not (Path(task_dir) / "task.toml").is_file():
        raise ValueError("managed Harbor mock requires a single task package")
    connection = managed_connection(target_file, uid, run_name)
    configure_task(task_dir, connection)
    # Native agent transcripts omit the initial user prompt. Supply the exact
    # task/shared context through Harbor's verifier-only tests directory.
    task_dir = Path(task_dir)
    shared_context = task_dir / "environment" / "CLAUDE.md"
    context = {
        "task_request": (task_dir / "instruction.md").read_text(),
        "shared_environment_context": shared_context.read_text() if shared_context.exists() else "",
    }
    tests = task_dir / "tests"
    tests.mkdir(exist_ok=True)
    (tests / "judge-context.json").write_text(json.dumps(context, indent=2))
    kwargs = config.setdefault("environment", {}).setdefault("kwargs", {})
    kwargs.update(mcp_run_uid=uid, mcp_run_name=run_name)
    return config
