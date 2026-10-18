"""Canonical MCP evaluation result model.

Defines the typed representation of an MCP evaluation across phases:

- **Phase 1** — static / build-time analysis
- **Phase 2** — deterministic contract / conformance testing
- **Phase 3** — behavioral testing (conditional on task suite availability)

Phases are **independent evaluation surfaces** — a Phase 1 failure does not
cascade to Phase 2 or Phase 3.  Each phase reports its own pass/fail.

Two Phase 2 checks (tool-name-rules, oauth-catalog-match) are *consumed* from
existing Compass facts and marked ``publishable=False`` so downstream
publishers know not to re-publish them.

This module is backend-agnostic: it defines the domain model and summary
loaders but has no knowledge of Compass, databases, or any specific reporting
backend.  Publish-specific code lives in
:mod:`abevalflow.mcp.checks.compass_publish`.
"""

from __future__ import annotations

import json
import logging
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, computed_field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase enum
# ---------------------------------------------------------------------------


class McpPhase(StrEnum):
    PHASE1 = "phase1"
    PHASE2 = "phase2"
    PHASE3 = "phase3"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONSUMED_CHECKS = frozenset({"tool-name-rules", "oauth-catalog-match"})

_HUMAN_NAMES_P1: dict[str, str] = {
    "mcp-secrets": "Secrets Management",
    "mcp-no-user-code": "No User-Code Execution",
    "mcp-license": "License Compliance",
}

_HUMAN_NAMES_P2: dict[str, str] = {
    "http-support": "HTTP Support",
    "protocol-compliance": "Protocol Compliance",
    "schema-conformance": "Schema Conformance",
    "tool-annotations": "Tool Annotations",
    "rate-limiting": "Rate Limiting",
    "response-size-limit": "Response Size Limit",
    "mandatory-timeouts": "Mandatory Timeouts",
    "openapi-conformance": "OpenAPI Conformance",
    "tool-name-rules": "Tool-Name Rules",
    "oauth-catalog-match": "OAuth Matches Catalog",
}

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class McpCheckResult(BaseModel):
    """Result of a single check within a phase."""

    check_id: str = Field(description="Gate or check name (e.g. mcp-secrets, http-support)")
    name: str = Field(description="Human-readable check name")
    passed: bool | None = Field(description="True / false, or None when the check was not evaluated")
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    status: str = Field(description="Three-state: pass / fail / not_evaluated")
    message: str = ""
    source: str = Field(default="pipeline", description="pipeline / compass / probe")
    publishable: bool = Field(default=True, description="False for consumed checks")


class McpPhaseResult(BaseModel):
    """Aggregate result of one evaluation phase."""

    phase: McpPhase
    passed: bool
    checks: list[McpCheckResult]

    @computed_field
    @property
    def checks_passed(self) -> int:
        return sum(1 for c in self.checks if c.status == "pass")

    @computed_field
    @property
    def checks_total(self) -> int:
        return len(self.checks)

    @computed_field
    @property
    def checks_evaluated(self) -> int:
        return sum(1 for c in self.checks if c.status in {"pass", "fail"})

    @computed_field
    @property
    def overall_score(self) -> float:
        evaluated = [c for c in self.checks if c.status in {"pass", "fail"}]
        if not evaluated:
            return 0.0
        return sum(c.score or 0.0 for c in evaluated) / len(evaluated)


class McpEvalResult(BaseModel):
    """Complete MCP evaluation result across all phases.

    Phases are independent — no hierarchy enforcement.
    """

    phase1: McpPhaseResult
    phase2: McpPhaseResult
    phase3: McpPhaseResult | None = None

    @computed_field
    @property
    def phases_passed(self) -> dict[str, bool]:
        result: dict[str, bool] = {
            "phase1": self.phase1.passed,
            "phase2": self.phase2.passed,
        }
        if self.phase3 is not None:
            result["phase3"] = self.phase3.passed
        return result


# ---------------------------------------------------------------------------
# Summary JSON loaders
# ---------------------------------------------------------------------------


def load_phase1_summary(path: Path) -> McpPhaseResult:
    """Parse ``phase1-summary.json`` into :class:`McpPhaseResult`."""
    data = json.loads(path.read_text())
    checks: list[McpCheckResult] = []
    for gate in data.get("gates", []):
        gate_id = gate["gate"]
        checks.append(
            McpCheckResult(
                check_id=gate_id,
                name=_HUMAN_NAMES_P1.get(gate_id, gate_id),
                passed=gate["passed"],
                score=gate.get("score", 1.0 if gate["passed"] else 0.0),
                status="pass" if gate["passed"] else "fail",
                message=gate.get("message", ""),
                source="pipeline",
                publishable=True,
            )
        )
    return McpPhaseResult(phase=McpPhase.PHASE1, passed=data["passed"], checks=checks)


def load_phase2_summary(path: Path) -> McpPhaseResult:
    """Parse ``phase2-summary.json`` into :class:`McpPhaseResult`.

    Consumed checks (tool-name-rules, oauth-catalog-match) are marked
    ``publishable=False``.
    """
    data = json.loads(path.read_text())
    checks: list[McpCheckResult] = []
    for check in data.get("checks", []):
        check_id = check["check"]
        status = check.get("status", "pass" if check["passed"] else "fail")
        is_consumed = check_id in CONSUMED_CHECKS
        source = check.get("source", "compass" if is_consumed else "probe")
        checks.append(
            McpCheckResult(
                check_id=check_id,
                name=_HUMAN_NAMES_P2.get(check_id, check_id),
                passed=True if status == "pass" else False if status == "fail" else None,
                score=None if status == "not_evaluated" else check.get("score", 1.0 if check["passed"] else 0.0),
                status=status,
                message=check.get("message", ""),
                source=source,
                publishable=not is_consumed,
            )
        )
    return McpPhaseResult(phase=McpPhase.PHASE2, passed=data["passed"], checks=checks)


def build_mcp_eval_result(
    phase1: McpPhaseResult,
    phase2: McpPhaseResult,
    phase3: McpPhaseResult | None = None,
) -> McpEvalResult:
    """Assemble phases into an :class:`McpEvalResult`.

    Phases are independent — no hierarchy enforcement.
    """
    return McpEvalResult(phase1=phase1, phase2=phase2, phase3=phase3)
