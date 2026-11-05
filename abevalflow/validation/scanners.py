"""External scanner execution with separate runtime and finding outcomes."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path

from abevalflow.results.run import StepResult


def local_scanner_executable(command: str) -> str:
    """Find bundled scanner entrypoints even when a tool install exposes only abeval."""
    if command == "python3":
        return sys.executable
    if command in {"skill-scanner", "harness-eval"}:
        installed = shutil.which(command, path=sysconfig.get_path("scripts"))
        if installed:
            return installed
    return command


def execute_scanner(
    name: str,
    command: list[str],
    outputs: list[Path],
    log: Path,
    timeout: int,
) -> StepResult:
    """Execute a scanner and require valid, structured evidence before accepting its verdict."""
    log.parent.mkdir(parents=True, exist_ok=True)
    for path in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
    try:
        with log.open("w") as stream:
            process = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        payloads = [json.loads(path.read_text()) for path in outputs]
        if any(not isinstance(payload, dict) or not isinstance(payload.get("findings"), list) for payload in payloads):
            raise ValueError("Scanner output must contain a findings array")
        findings = [f for payload in payloads for f in payload["findings"]]
        if any(not isinstance(f, dict) for f in findings):
            raise ValueError("Scanner findings must be JSON objects")
        if any(
            str(f.get("severity", "info")).lower() not in {"info", "low", "medium", "high", "critical"}
            for f in findings
        ):
            raise ValueError("Scanner findings contain an unsupported severity")
        high = any(str(f.get("severity", "")).lower() in {"high", "critical"} for f in findings)
        if process.returncode and not (process.returncode == 1 and findings):
            raise ValueError(f"Scanner exited {process.returncode}; see {log}")
        return StepResult(
            name=name,
            tier=1,
            verdict="fail" if high else ("warn" if findings else "pass"),
            artifacts=[str(p) for p in outputs] + [str(log)],
            details={"findings": findings, "exit_code": process.returncode},
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return StepResult(
            name=name, tier=1, status="error", verdict="unavailable", reason=str(exc), artifacts=[str(log)]
        )


def scanner_command(config, name: str, command: list[str]) -> list[str]:
    """Select native scanner execution or an isolated, pinned scanner container."""
    import os

    if config.scanner_runtime == "local":
        return [local_scanner_executable(command[0]), *command[1:]]
    image = config.security_image if name == "security" else config.quality_image
    if not image:
        raise ValueError(f"Container scanning requires a {name} image")
    source = config.output / "source"
    reports = config.output / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    translated = [part.replace(str(source), "/source").replace(str(reports), "/reports") for part in command]
    runtime = [
        config.scanner_runtime,
        "run",
        "--rm",
        "--network=none",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "-e",
        "HOME=/tmp",
        "-v",
        f"{source}:/source:ro",
        "-v",
        f"{reports}:/reports:rw",
    ]
    if config.scanner_runtime == "podman":
        runtime += ["--userns=keep-id"]
    return runtime + [image] + translated
