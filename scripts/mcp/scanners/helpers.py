"""Scanner I/O helpers for MCP Phase 1.

Provides ``run_tool`` (shell out to a scanner CLI) and ``write_scan`` (write
normalized scan JSON). Finding construction (``make_finding``) lives in
``abevalflow.mcp.types`` — scanners import it from there.

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
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def write_scan(
    scan_path: Path,
    scanner: str,
    target: Path,
    findings: list[dict[str, Any]],
    *,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write a normalized scan report to ``scan_path``.

    ``extra`` merges additional top-level metadata (e.g. a ``coverage`` block)
    into the payload. The Phase 1 gates read only ``findings``, so extra keys are
    informational and never change pass/fail. The canonical keys always win, so a
    caller cannot accidentally clobber ``findings`` via ``extra``.
    """
    scan_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = dict(extra or {})
    payload.update(
        {
            "scanner": scanner,
            "target": str(target),
            "findings": findings,
        }
    )
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
