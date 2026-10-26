"""Unified scorecard schema for combining gate results.

The scorecard aggregates results from all gates (engine, security, quality)
and applies a configurable policy to produce a single recommendation.

The scorecard also computes certification levels (Foundational, Trusted, Certified)
based on which checks pass. See abevalflow.certification for level definitions.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field

from abevalflow.certification import (
    CertificationLevel,
    CertificationResult,
)
from abevalflow.compass_facts import CertificationFactPushResult, FactPushResult
from abevalflow.gates.base import GateMode, GateResult
from abevalflow.schemas import CombinationMode, GatePolicy


class Recommendation(StrEnum):
    """Unified scorecard recommendation."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class Scorecard(BaseModel):
    """Unified scorecard combining all gate results.

    The scorecard is the single source of truth for submission evaluation,
    aggregating engine, security, and quality gate results with policy.
    """

    submission_name: str = Field(description="Name of the evaluated submission")
    pipeline_run_id: str = Field(description="Tekton PipelineRun ID")
    eval_engine: str = Field(description="Primary evaluation engine used")

    gates: list[GateResult] = Field(
        default_factory=list,
        description="All gate results in standard format",
    )

    policy: GatePolicy = Field(
        default_factory=GatePolicy,
        description="Policy that was applied for this evaluation",
    )

    recommendation: Recommendation = Field(description="Unified verdict: pass, warn, or fail")
    evidence_complete: bool = Field(default=True, description="Whether all selected evaluation operations completed")
    recommendation_reason: str = Field(
        description="Human-readable explanation of the recommendation",
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the scorecard was created",
    )

    provenance: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional provenance metadata (commit SHA, branch, etc.)",
    )

    fact_push_results: list[FactPushResult] = Field(
        default_factory=list,
        description="Results of pushing gate facts to Compass (empty if disabled)",
    )

    certification: CertificationResult | None = Field(
        default=None,
        description="Certification level results (Foundational, Trusted, Certified)",
    )

    certification_fact_push_results: list[CertificationFactPushResult] = Field(
        default_factory=list,
        description="Results of pushing certification facts to Compass",
    )

    @computed_field
    @property
    def highest_certification(self) -> CertificationLevel:
        """Highest certification level achieved."""
        if self.certification is None:
            return CertificationLevel.NONE
        return self.certification.highest_level

    @computed_field
    @property
    def gates_passed(self) -> int:
        """Count of gates that passed."""
        return sum(1 for g in self.gates if g.passed)

    @computed_field
    @property
    def gates_failed(self) -> int:
        """Count of gates that failed."""
        return sum(1 for g in self.gates if not g.passed)

    @computed_field
    @property
    def blocking_gates_passed(self) -> int:
        """Count of blocking gates that passed."""
        return sum(1 for g in self.gates if g.mode == GateMode.BLOCK and g.passed)

    @computed_field
    @property
    def blocking_gates_failed(self) -> int:
        """Count of blocking gates that failed."""
        return sum(1 for g in self.gates if g.mode == GateMode.BLOCK and not g.passed)


def apply_combination_logic(
    gates: list[GateResult],
    policy: GatePolicy,
) -> tuple[Recommendation, str]:
    """Apply policy combination logic to gate results.

    Args:
        gates: List of gate results to combine
        policy: Policy defining combination mode and gate configs

    Returns:
        Tuple of (recommendation, reason)
    """
    if not gates:
        return Recommendation.FAIL, "No gates evaluated"

    blocking_gates = [g for g in gates if g.mode == GateMode.BLOCK]
    warn_gates = [g for g in gates if g.mode == GateMode.WARN]

    if policy.combination == CombinationMode.ALL_PASS:
        failed_blocking = [g for g in blocking_gates if not g.passed]
        if failed_blocking:
            names = ", ".join(g.gate_name for g in failed_blocking)
            return Recommendation.FAIL, f"Blocking gates failed: {names}"

        failed_warn = [g for g in warn_gates if not g.passed]
        if failed_warn:
            names = ", ".join(g.gate_name for g in failed_warn)
            return Recommendation.WARN, f"Warning gates failed: {names}"

        return Recommendation.PASS, "All gates passed"

    elif policy.combination == CombinationMode.ANY_PASS:
        if not blocking_gates:
            return Recommendation.PASS, "No blocking gates configured"

        passed_blocking = [g for g in blocking_gates if g.passed]
        if passed_blocking:
            names = ", ".join(g.gate_name for g in passed_blocking)
            return Recommendation.PASS, f"Blocking gates passed: {names}"

        names = ", ".join(g.gate_name for g in blocking_gates)
        return Recommendation.FAIL, f"No blocking gates passed: {names}"

    elif policy.combination == CombinationMode.WEIGHTED:
        if not blocking_gates:
            total_score = sum(g.score for g in gates) / len(gates) if gates else 0.0
        else:
            total_weight = 0.0
            weighted_score = 0.0
            for g in blocking_gates:
                gate_policy = policy.get_gate_policy(g.gate_name)
                weighted_score += g.score * gate_policy.weight
                total_weight += gate_policy.weight
            total_score = weighted_score / total_weight if total_weight > 0 else 0.0

        if total_score >= 0.7:
            return Recommendation.PASS, f"Weighted score {total_score:.2f} >= 0.70"
        elif total_score >= 0.5:
            return Recommendation.WARN, f"Weighted score {total_score:.2f} between 0.50-0.70"
        else:
            return Recommendation.FAIL, f"Weighted score {total_score:.2f} < 0.50"

    return Recommendation.FAIL, f"Unknown combination mode: {policy.combination}"
