"""Shared helpers for MCP Phase 1 scanner scripts.

All Phase 1 scanners emit the same JSON shape so the Phase 1 gates (which reuse
``SecurityGate.evaluate_scan_json``) can read them uniformly::

    {
      "scanner": "<tool name>",
      "target": "<scanned path>",
      "findings": [
        {"severity": "high", "message": "...", "rule_id": "...", "file_path": "..."},
        ...
      ]
    }

``severity`` must be one of the values understood by ``abevalflow.gates.base``:
critical / high / medium / low / info.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

VALID_SEVERITIES = ("critical", "high", "medium", "low", "info")


def normalize_severity(value: str | None, default: str = "info") -> str:
    """Coerce an arbitrary severity string into a known severity level."""
    if not value:
        return default
    sev = value.strip().lower()
    return sev if sev in VALID_SEVERITIES else default


def make_finding(
    *,
    severity: str,
    message: str,
    rule_id: str,
    file_path: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a single normalized finding dict."""
    finding: dict[str, Any] = {
        "severity": normalize_severity(severity),
        "message": message,
        "rule_id": rule_id,
        "file_path": file_path,
    }
    finding.update(extra)
    return finding


def write_scan(
    scan_path: Path,
    scanner: str,
    target: Path,
    findings: list[dict[str, Any]],
) -> None:
    """Write a normalized scan report to ``scan_path``."""
    scan_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "scanner": scanner,
        "target": str(target),
        "findings": findings,
    }
    scan_path.write_text(json.dumps(payload, indent=2))
    logger.info("Wrote %d findings to %s", len(findings), scan_path)


def run_tool(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a scanner CLI, capturing stdout/stderr as text.

    The return code is NOT checked here - most scanners exit non-zero when they
    find issues, which is expected. Callers inspect ``returncode`` explicitly to
    distinguish "findings present" from "tool failed to run".
    """
    logger.info("Running: %s", " ".join(cmd))
    return subprocess.run(  # noqa: S603 - cmd is built from trusted, fixed tool args
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )
