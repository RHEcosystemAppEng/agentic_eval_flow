"""Base schemas for gate results.

Gates are evaluation checkpoints that produce standardized results.
Three types exist:
- Engine gates: the AEH evaluation adapter
- Security gates: security scanners (Cisco, Snyk)
- Quality gates: quality reviewers (LLM review)

Each gate produces a GateResult with a normalized score and pass/fail status.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class GateType(StrEnum):
    """Type of gate in the unified scorecard."""

    VALIDATION = "validation"
    ENGINE = "engine"
    SECURITY = "security"
    QUALITY = "quality"
    BEHAVIORAL = "behavioral"


class GateMode(StrEnum):
    """Gate enforcement mode."""

    DISABLED = "disabled"
    WARN = "warn"
    BLOCK = "block"


class Severity(StrEnum):
    """Finding severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Finding(BaseModel):
    """A single finding from a gate evaluation.

    Findings represent issues, warnings, or informational notes discovered
    during gate evaluation. Used by security and quality gates primarily.
    """

    severity: Severity = Field(description="Severity level of the finding")
    message: str = Field(description="Human-readable description of the finding")
    location: str | None = Field(
        default=None,
        description="File path or location where the finding was detected",
    )
    rule_id: str | None = Field(
        default=None,
        description="Identifier of the rule that triggered this finding",
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Additional structured details about the finding",
    )


class GateResult(BaseModel):
    """Standardized result from any gate evaluation.

    All gates (engine, security, quality) produce this common format,
    enabling unified scorecard aggregation and policy enforcement.

    Gate naming conventions:
        - gate_name: Category name (evaluation, security, quality) used in Compass facts
        - policy_key: Implementation name (harbor, cisco, llm-review) used for policy lookup

    This separation allows category-based naming in external systems (Compass) while
    maintaining implementation-specific policy configuration.
    """

    gate_type: GateType = Field(description="Type of gate: validation, engine, security, or quality")
    gate_name: str = Field(description="Category name of the gate (e.g., 'evaluation', 'security', 'quality')")
    policy_key: str | None = Field(
        default=None,
        description="Implementation name for policy lookup (e.g., 'harbor', 'cisco'). "
        "Falls back to gate_name if not set.",
    )
    passed: bool = Field(description="Whether the gate passed based on its internal criteria")
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Normalized score from 0.0 (worst) to 1.0 (best)",
    )
    mode: GateMode = Field(
        default=GateMode.WARN,
        description="Enforcement mode that was applied to this gate",
    )
    threshold: float | None = Field(
        default=None,
        description="Threshold that was used to determine pass/fail (if applicable)",
    )
    findings: list[Finding] = Field(
        default_factory=list,
        description="List of findings from the gate evaluation",
    )
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Engine-specific payload (AnalysisResult, MCPCheckerResult, etc.)",
    )
    message: str | None = Field(
        default=None,
        description="Human-readable summary of the gate result",
    )
    evaluated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the gate was evaluated",
    )

    def get_policy_key(self) -> str:
        """Get the key used for policy lookup.

        Returns policy_key if set, otherwise falls back to gate_name.
        """
        return self.policy_key or self.gate_name
