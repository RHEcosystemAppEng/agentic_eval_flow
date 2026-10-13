"""Helper functions for the MCP evaluation pipeline.

Finding builders, severity normalization, and check-result file I/O. Used by
both Phase 1 scanners (via ``make_finding``) and Phase 2 checks/probes (via
``make_finding`` + ``write_check_result``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from abevalflow.mcp.types import CheckOutcome

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Severity helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Check-result file I/O
# ---------------------------------------------------------------------------


def result_filename(check: str) -> str:
    """Filename a check's result is written to (and read back by its gate)."""
    return f"{check}-check.json"


def write_check_result(reports_dir: Path, outcome: CheckOutcome) -> Path:
    """Write one check outcome to ``<reports_dir>/<check>-check.json``."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / result_filename(outcome.check)
    path.write_text(json.dumps(outcome.to_dict(), indent=2))
    logger.info("Wrote %s check result (%s) to %s", outcome.check, outcome.status, path)
    return path
