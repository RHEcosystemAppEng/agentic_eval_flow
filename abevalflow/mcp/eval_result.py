"""Canonical MCP evaluation result model and phase summary loader.

The domain model is backend-agnostic. Compass publishing lives in
:mod:`abevalflow.mcp.checks.compass_publish`.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase-specific interpretation config
# ---------------------------------------------------------------------------

PHASE_CHECK_NAMES: dict[str, dict[str, str]] = {
    "phase1": {
        "mcp-secrets": "Secrets Management",
        "mcp-no-user-code": "No User-Code Execution",
        "mcp-license": "License Compliance",
    },
    "phase2": {
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
    },
}

PHASE_CONSUMED_CHECKS: dict[str, set[str]] = {
    "phase2": {"tool-name-rules", "oauth-catalog-match"},
}

PHASE_SOURCE_DEFAULTS: dict[str, str] = {
    "phase1": "pipeline",
    "phase2": "probe",
}

PHASE_CHECK_SOURCE_DEFAULTS: dict[str, dict[str, str]] = {
    "phase2": {
        "tool-name-rules": "compass",
        "oauth-catalog-match": "compass",
    },
}

DEFAULT_SOURCE = "pipeline"
VALID_CHECK_STATUSES = frozenset({"pass", "fail", "not_evaluated"})
PHASE_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def validate_phase_name(phase_name: str) -> str:
    """Validate phase-name syntax without restricting names to configured phases."""
    if not PHASE_NAME_PATTERN.fullmatch(phase_name):
        raise ValueError("phase name must contain only letters, digits, underscores, and hyphens")
    return phase_name


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
    source: str = Field(default=DEFAULT_SOURCE, description="pipeline / compass / probe")


class McpPhaseResult(BaseModel):
    """Aggregate result of one evaluation phase."""

    phase: str
    passed: bool
    checks: list[McpCheckResult]

    @field_validator("phase")
    @classmethod
    def phase_name_has_valid_format(cls, phase_name: str) -> str:
        return validate_phase_name(phase_name)

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
    """Complete MCP evaluation result across any supplied phases."""

    phases: dict[str, McpPhaseResult]

    @model_validator(mode="after")
    def phases_are_nonempty_and_keyed_by_name(self) -> McpEvalResult:
        if not self.phases:
            raise ValueError("at least one phase result is required")
        for phase_name, phase_result in self.phases.items():
            if phase_name != phase_result.phase:
                raise ValueError(f"phase mapping key {phase_name!r} does not match result name {phase_result.phase!r}")
        return self

    @computed_field
    @property
    def phases_passed(self) -> dict[str, bool]:
        return {name: phase.passed for name, phase in self.phases.items()}


# ---------------------------------------------------------------------------
# Summary JSON loader
# ---------------------------------------------------------------------------


def load_phase_summary(path: Path) -> McpPhaseResult:
    """Load a standardized phase summary JSON file.

    Unconfigured phase names remain valid; config maps only add phase-specific
    labels, defaults, and Compass fact references.
    """
    data = json.loads(path.read_text())
    phase_name = data["phase"]
    checks: list[McpCheckResult] = []

    for check in data.get("checks", []):
        check_id = check["check"]
        status = check.get("status")
        if status is None:
            status = "pass" if check["passed"] else "fail"
        if status not in VALID_CHECK_STATUSES:
            raise ValueError(f"Invalid status {status!r} for check {check_id!r} in phase {phase_name!r}")

        default_source = PHASE_CHECK_SOURCE_DEFAULTS.get(phase_name, {}).get(
            check_id,
            PHASE_SOURCE_DEFAULTS.get(phase_name, DEFAULT_SOURCE),
        )
        passed = True if status == "pass" else False if status == "fail" else None
        default_score = 1.0 if status == "pass" else 0.0
        checks.append(
            McpCheckResult(
                check_id=check_id,
                name=PHASE_CHECK_NAMES.get(phase_name, {}).get(check_id, check_id),
                passed=passed,
                score=None if status == "not_evaluated" else check.get("score", default_score),
                status=status,
                message=check.get("message", ""),
                source=check.get("source") or default_source,
            )
        )

    return McpPhaseResult(phase=phase_name, passed=data["passed"], checks=checks)


def build_mcp_eval_result(phases: Iterable[McpPhaseResult]) -> McpEvalResult:
    """Assemble any nonempty set of unique phase results."""
    phase_map: dict[str, McpPhaseResult] = {}
    for phase in phases:
        if phase.phase in phase_map:
            raise ValueError(f"Duplicate phase result: {phase.phase!r}")
        phase_map[phase.phase] = phase
    return McpEvalResult(phases=phase_map)
