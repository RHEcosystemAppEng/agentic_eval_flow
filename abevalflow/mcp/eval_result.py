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
from typing import Literal

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
    "tool-name-rules": "Tool Name Rules",
    "oauth-catalog-match": "OAuth Catalog Match",
}

_HUMAN_NAMES_P3: dict[str, str] = {
    "tool-correctness": "Tool Correctness",
    "edge-case-handling": "Edge Case Handling",
    "sensitive-data-redaction": "Sensitive Data Redaction",
}

SummaryStatus = Literal["pass", "fail", "not_evaluated"]


class StaticEvalGateSummary(BaseModel):
    gate: str
    passed: bool
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    findings: int = Field(default=0, ge=0)
    message: str = ""


class StaticEvalSummary(BaseModel):
    phase: Literal["static-eval"] | None = None
    passed: bool
    gates: list[StaticEvalGateSummary]


class EvalSummaryCheck(BaseModel):
    check: str
    status: SummaryStatus | None = None
    passed: bool
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    findings: int = Field(default=0, ge=0)
    source: str | None = None
    message: str = ""


class ConformanceEvalSummary(BaseModel):
    phase: Literal["conformance-eval"] | None = None
    passed: bool
    counts: dict[str, int] | None = None
    checks: list[EvalSummaryCheck]


class BehavioralEvalSummary(BaseModel):
    phase: Literal["behavioral-eval"] | None = None
    passed: bool
    counts: dict[str, int] | None = None
    checks: list[EvalSummaryCheck]


def load_phase1_summary(path: Path) -> McpPhaseResult:
    """Parse a static evaluation summary into :class:`McpPhaseResult`."""
    summary = StaticEvalSummary.model_validate(json.loads(path.read_text()))
    checks: list[McpCheckResult] = []
    for gate in summary.gates:
        gate_id = gate.gate
        checks.append(
            McpCheckResult(
                check_id=gate_id,
                name=_HUMAN_NAMES_P1.get(gate_id, gate_id),
                passed=gate.passed,
                score=gate.score if gate.score is not None else (1.0 if gate.passed else 0.0),
                status="pass" if gate.passed else "fail",
                message=gate.message,
                source="pipeline",
                publishable=True,
            )
        )
    return McpPhaseResult(phase=McpPhase.PHASE1, passed=summary.passed, checks=checks)


def load_phase2_summary(path: Path) -> McpPhaseResult:
    """Parse a conformance evaluation summary into :class:`McpPhaseResult`.

    Consumed checks (tool-name-rules, oauth-catalog-match) are marked
    ``publishable=False``.
    """
    summary = ConformanceEvalSummary.model_validate(json.loads(path.read_text()))
    checks: list[McpCheckResult] = []
    for check in summary.checks:
        check_id = check.check
        status = check.status or ("pass" if check.passed else "fail")
        is_consumed = check_id in CONSUMED_CHECKS
        source = check.source or ("compass" if is_consumed else "probe")
        passed = True if status == "pass" else False if status == "fail" else None
        score = (
            None
            if status == "not_evaluated"
            else (check.score if check.score is not None else (1.0 if check.passed else 0.0))
        )
        checks.append(
            McpCheckResult(
                check_id=check_id,
                name=_HUMAN_NAMES_P2.get(check_id, check_id),
                passed=passed,
                score=score,
                status=status,
                message=check.message,
                source=source,
                publishable=not is_consumed,
            )
        )
    return McpPhaseResult(phase=McpPhase.PHASE2, passed=summary.passed, checks=checks)


def load_phase3_summary(path: Path) -> McpPhaseResult:
    """Parse a behavioral evaluation summary into :class:`McpPhaseResult`."""
    summary = BehavioralEvalSummary.model_validate(json.loads(path.read_text()))
    checks: list[McpCheckResult] = []
    for check in summary.checks:
        status = check.status or ("pass" if check.passed else "fail")
        passed = True if status == "pass" else False if status == "fail" else None
        score = (
            None
            if status == "not_evaluated"
            else (check.score if check.score is not None else (1.0 if check.passed else 0.0))
        )
        check_id = check.check
        checks.append(
            McpCheckResult(
                check_id=check_id,
                name=_HUMAN_NAMES_P3.get(check_id, check_id),
                passed=passed,
                score=score,
                status=status,
                message=check.message,
                source=check.source or "mcpchecker",
                publishable=True,
            )
        )
    return McpPhaseResult(phase=McpPhase.PHASE3, passed=summary.passed, checks=checks)


def build_mcp_eval_result(
    phase1: McpPhaseResult,
    phase2: McpPhaseResult,
    phase3: McpPhaseResult | None = None,
) -> McpEvalResult:
    """Assemble phases into an :class:`McpEvalResult`.

    Phases are independent — no hierarchy enforcement.
    """
    return McpEvalResult(phase1=phase1, phase2=phase2, phase3=phase3)
