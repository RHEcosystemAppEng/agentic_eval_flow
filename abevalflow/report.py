"""Pydantic models for A/B evaluation analysis reports.

These models define the structure of the JSON report produced by
``scripts/analyze.py``. They serve as the contract between the analysis
step and any downstream consumers (DB persistence, PR comments, dashboards).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, computed_field


class Recommendation(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class SecuritySeverity(StrEnum):
    """Severity levels for security findings."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class SecurityFinding(BaseModel):
    """A single security finding from a scanner."""

    rule_id: str = Field(description="Scanner rule identifier")
    severity: SecuritySeverity
    message: str = Field(description="Human-readable description of the finding")
    file_path: str | None = Field(default=None, description="File where the finding was detected")
    line_number: int | None = Field(default=None, description="Line number if applicable")
    scanner: str = Field(description="Scanner that produced this finding")


class ScanMode(StrEnum):
    """Security scan mode for results.

    Note: DISABLED is not included here because if a scan result exists,
    scanning was not disabled. The submission-level SecurityScanMode enum
    in schemas.py includes DISABLED for configuration purposes.
    """

    WARN = "warn"
    BLOCK = "block"


class SecurityScanResult(BaseModel):
    """Results from security scanning step."""

    scanner: str = Field(description="Scanner identifier (e.g. cisco)")
    scan_mode: ScanMode = Field(description="Scan mode used: warn or block")
    findings: list[SecurityFinding] = Field(default_factory=list)
    passed: bool = Field(
        default=True,
        description="True if scan passed (warn mode always passes; block mode fails on HIGH/CRITICAL)",
    )
    sarif_path: str | None = Field(default=None, description="Path to SARIF output file")
    json_path: str | None = Field(default=None, description="Path to JSON output file")
    scan_duration_seconds: float | None = Field(default=None, description="Time taken to run scan")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def severity_counts(self) -> dict[str, int]:
        """Count of findings by severity level, derived from findings list."""
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for finding in self.findings:
            sev = finding.severity.value
            if sev in counts:
                counts[sev] += 1
        return counts


class TrialResult(BaseModel):
    """A single trial's outcome."""

    trial_name: str
    reward: float | None = Field(
        default=None,
        description="Continuous reward score (0.0-1.0). None if the trial produced no parseable result.",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return self.reward is not None and self.reward > 0.0


class VariantSummary(BaseModel):
    """Aggregate statistics for one variant's trials."""

    n_trials: int = 0
    n_passed: int = 0
    n_failed: int = 0
    n_errors: int = Field(
        default=0,
        description="Trials with missing or unparseable results",
    )
    pass_rate: float = 0.0
    mean_reward: float | None = None
    median_reward: float | None = None
    std_reward: float | None = None


class Provenance(BaseModel):
    """Run provenance metadata for reproducibility and forge/trace join keys.

    See Docs/provenance_forge_pointers.md for the cross-project join contract.
    """

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    commit_sha: str | None = None
    pipeline_run_id: str | None = None
    pipeline_run_url: str | None = Field(
        default=None,
        description="CI pipeline run URL (convenience; complements pipeline_run_id)",
    )
    ref_name: str | None = Field(
        default=None,
        description="Branch or tag short name (OTel vcs.ref.head.name)",
    )
    treatment_image_ref: str | None = None
    control_image_ref: str | None = None
    harbor_fork_revision: str | None = None
    eval_engine: str = Field(
        default="harbor",
        description="Evaluation engine used: 'harbor', 'ase', 'a2a', 'aeh', 'mcpchecker', or 'both'",
    )
    repository_url: str | None = Field(
        default=None,
        description="Canonical repo URL (OTel vcs.repository.url.full)",
    )
    change_id: str | None = Field(
        default=None,
        description="PR/MR number (OTel vcs.change.id)",
    )
    trace_id: str | None = Field(
        default=None,
        description="W3C or MLflow trace id for the evaluation run",
    )
    session_id: str | None = Field(
        default=None,
        description="Session / conversation id (OTel gen_ai.conversation.id)",
    )
    eval_run_id: str | None = Field(
        default=None,
        description="Eval engine batch/case run id when applicable",
    )
    harness_fingerprint: str | None = Field(
        default=None,
        description="Harness/config content hash for reproducibility",
    )
    forge_platform: str | None = Field(
        default=None,
        description="Git hosting platform: github | gitlab | bitbucket | forgejo",
    )


class DegradationResult(BaseModel):
    """Historical degradation check against previous evaluation runs."""

    degraded: bool
    message: str
    threshold: float | None = None
    previous_pass_rate: float | None = None
    current_pass_rate: float | None = None


class AnalysisSummary(BaseModel):
    """Comparison statistics between treatment and control."""

    related_pr: str | None = Field(
        default=None,
        description="URL of the PR that triggered this evaluation",
    )
    llm: str | None = Field(
        default=None,
        description="LLM model used for evaluation (e.g. 'Claude Sonnet 4.6 (vertex_ai)')",
    )
    treatment: VariantSummary
    control: VariantSummary
    uplift: float = Field(description="treatment pass_rate - control pass_rate (secondary; see mean_reward_gap)")
    mean_reward_gap: float | None = Field(
        default=None,
        description="treatment mean_reward - control mean_reward",
    )
    ttest_p_value: float | None = Field(
        default=None,
        description="Welch's t-test p-value on continuous reward scores",
    )
    fisher_p_value: float | None = Field(
        default=None,
        description="Fisher's exact test p-value on binary pass/fail counts",
    )
    recommendation: Recommendation


class EdgeCaseResult(BaseModel):
    """Result of a single edge case evaluation."""

    name: str = Field(description="Edge case name (derived from filename)")
    summary: VariantSummary | None = Field(
        default=None,
        description="Trial stats (present for Harbor-based eval, absent for ASE-based eval)",
    )
    passed: bool = Field(description="Whether the edge case passed verification")
    score: float | None = Field(default=None, description="Edge case score (0.0-1.0)")
    source: str | None = Field(default=None, description="Origin: 'generated' or 'submitter'")


class PairwiseComparisonResult(BaseModel):
    """AEH pairwise LLM-judge comparison (from score.py pairwise)."""

    run_a: str = ""
    run_b: str = ""
    cases_compared: int = 0
    wins_a: int = 0
    wins_b: int = 0
    ties: int = 0
    errors: int = 0
    win_rate: float = 0.0
    per_case: list[dict[str, Any]] = Field(default_factory=list)
    stability: dict[str, Any] | None = None


class AnalysisResult(BaseModel):
    """Top-level report model written to report.json."""

    submission_name: str
    provenance: Provenance
    summary: AnalysisSummary
    trials: dict[str, list[TrialResult]] = Field(
        description="Per-variant list of trial outcomes, keyed by 'treatment'/'control'",
    )
    security_scans: list[SecurityScanResult] = Field(
        default_factory=list,
        description="Results from security scanners. Empty if scanning disabled.",
    )
    degradation: DegradationResult | None = Field(
        default=None,
        description="Historical degradation check result. Present when monitoring ran.",
    )
    edge_case_results: list[EdgeCaseResult] = Field(
        default_factory=list,
        description="Results from edge case evaluations. Empty if no edge cases defined.",
    )
    pairwise: PairwiseComparisonResult | None = Field(
        default=None,
        description="AEH pairwise comparison results. Present for aeh-mode=pairwise.",
    )
