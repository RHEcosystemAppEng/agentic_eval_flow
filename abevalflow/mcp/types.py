"""Shared types and helpers for the MCP evaluation pipeline.

Defines the data shapes both phases use:

- **Phase 1** findings: ``make_finding()`` builds the dicts that scanners write
  and Phase 1 gates read.
- **Phase 2** check outcomes: ``CheckOutcome`` carries a three-state result
  (pass / fail / not_evaluated) that the Phase 2 gate reads.

Phase 2 uses the three-state model because a check that cannot produce a
meaningful signal (server unreachable, no OpenAPI contract, a capability the
server does not expose) must report ``not_evaluated`` rather than a false pass
or fail (ADR: three-state reporting).

Every Phase 2 check — whether a live probe or a value consumed from Compass —
writes the same JSON shape so the Phase 2 gates can read them uniformly::

    {
      "check": "protocol-compliance",
      "status": "pass" | "fail" | "not_evaluated",
      "source": "probe" | "compass",
      "reason": "human-readable summary",
      "findings": [ {"severity": ..., "message": ..., "rule_id": ...}, ... ]
    }
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Severity helpers (used by Phase 1 scanners and Phase 2 checks)
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
# Phase 2 three-state model
# ---------------------------------------------------------------------------

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_NOT_EVALUATED = "not_evaluated"
VALID_STATUSES = (STATUS_PASS, STATUS_FAIL, STATUS_NOT_EVALUATED)


@dataclass
class CheckOutcome:
    """Result of a single Phase 2 check."""

    check: str
    status: str
    reason: str = ""
    source: str = "probe"
    findings: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"invalid status {self.status!r}; expected one of {VALID_STATUSES}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "status": self.status,
            "source": self.source,
            "reason": self.reason,
            "findings": self.findings,
        }


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
