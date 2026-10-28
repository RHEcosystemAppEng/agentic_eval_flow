"""Pydantic models for submission metadata validation.

The schema defines the structure of metadata.yaml files that accompany
submissions. The schema_version field tracks the format version so the
pipeline can handle older submissions gracefully when the schema evolves
(e.g., new fields added, defaults changed).

Current schema version: 1.0
"""

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

CURRENT_SCHEMA_VERSION = "1.0"

_SCHEMA_VERSION_RE = re.compile(r"\d+\.\d+")
_OCI_NAME_RE = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*")


class EvalEngine(StrEnum):
    AEH = "aeh"


EVAL_ENGINE_CLI_CHOICES = tuple(e.value for e in EvalEngine)


class SecurityScanMode(StrEnum):
    DISABLED = "disabled"
    WARN = "warn"
    BLOCK = "block"


class AehMode(StrEnum):
    """AEH evaluation mode. Mirrors submission validation --aeh-mode choices.

    'single' evaluates the skilled agent alone against eval.yaml's own
    threshold (the catalog-wide default — see .tekton/*.yaml AEH_MODE
    param). 'pairwise' additionally runs a vanilla-vs-skilled control/
    treatment A/B comparison via eval-control.yaml / eval-treatment.yaml
    (aeh_control_config / aeh_treatment_config below) and reports win-rate
    uplift. Declaring this HERE, per-submission, lets one skill opt into
    pairwise without flipping the pipeline-wide default for every other
    skill's leg of the same Matrix fan-out — see aeh-skill-eval.yaml's
    validate step, which prefers this value over the pipeline's own
    aeh-mode param when set.
    """

    SINGLE = "single"
    PAIRWISE = "pairwise"


# Import GateMode from canonical location to avoid duplication
from abevalflow.gates.base import GateMode  # noqa: E402


class CombinationMode(StrEnum):
    """How to combine multiple gate results in the scorecard."""

    ALL_PASS = "all_pass"
    ANY_PASS = "any_pass"
    WEIGHTED = "weighted"


class PushFactsConfig(BaseModel):
    """Configuration for pushing gate results to Compass Facts API.

    When endpoint is set, gates with push_fact=True will POST their
    results as Soundcheck facts after evaluation completes.

    Example:
        push_facts:
          endpoint: https://compass.stage.redhat.com/api/soundcheck/facts/
          entity_ref: component:default/abevalflow
          fact_ref_prefix: catalog:default/abevalflow_
          bearer_token: ${COMPASS_API_TOKEN}  # from env/secret
    """

    endpoint: str = Field(
        description="Compass Facts API URL",
    )
    entity_ref: str = Field(
        description="Compass entity reference (e.g. component:default/abevalflow)",
    )
    fact_ref_prefix: str = Field(
        default="catalog:default/abevalflow_",
        description="Prefix for fact references. Gate name is appended",
    )
    bearer_token: str | None = Field(
        default=None,
        description=(
            "Bearer token for Compass API authentication. "
            "Can use environment variable substitution (e.g. ${COMPASS_API_TOKEN}). "
            "When set, an Authorization: Bearer header is sent with each request."
        ),
    )


class GatePolicyItem(BaseModel):
    """Policy configuration for a single gate."""

    mode: GateMode = Field(
        default=GateMode.WARN,
        description="Enforcement mode: disabled, warn, or block",
    )
    threshold: float | None = Field(
        default=None,
        description="Score threshold for pass/fail (gate-specific default if None)",
    )
    weight: float = Field(
        default=1.0,
        ge=0.0,
        description="Weight for weighted combination mode",
    )
    push_fact: bool = Field(
        default=False,
        description="Push gate result to Compass Facts API (requires push_facts config)",
    )


class GatePolicy(BaseModel):
    """Policy for gate evaluation and combination.

    Can be embedded in metadata.yaml under 'gate_policy' key.
    When not specified, defaults are applied.

    Gate naming:
        Gates are configured by category: evaluation, security, quality.
        The specific implementation (harbor, cisco, llm-review) is determined
        automatically based on the eval-engine and available scanners.

    Example in metadata.yaml:

        gate_policy:
          default_mode: warn
          combination: all_pass
          push_facts:
            endpoint: https://compass.stage.redhat.com/api/soundcheck/facts/
            entity_ref: component:default/abevalflow
            bearer_token: ${COMPASS_API_TOKEN}
          gates:
            evaluation:   # category name (auto-selects engine based on eval-engine param)
              mode: block
              threshold: 0.0
              push_fact: true
            security:     # category name (uses cisco scanner)
              mode: block
              push_fact: true
            quality:      # category name (uses llm-review)
              mode: warn
              threshold: 0.6
    """

    default_mode: GateMode = Field(
        default=GateMode.WARN,
        description="Default mode for gates not explicitly configured",
    )
    combination: CombinationMode = Field(
        default=CombinationMode.ALL_PASS,
        description="How to combine gate results into final recommendation",
    )
    push_facts: PushFactsConfig | None = Field(
        default=None,
        description="Compass Facts API configuration. None disables fact pushing.",
    )
    gates: dict[str, GatePolicyItem] = Field(
        default_factory=dict,
        description="Per-gate policy overrides keyed by gate name",
    )

    def get_gate_policy(self, gate_name: str) -> GatePolicyItem:
        """Get policy for a specific gate, falling back to defaults."""
        if gate_name in self.gates:
            return self.gates[gate_name]
        return GatePolicyItem(mode=self.default_mode)

    def is_enabled(self, gate_name: str) -> bool:
        """Check if a gate is enabled (not disabled)."""
        policy = self.get_gate_policy(gate_name)
        return policy.mode != GateMode.DISABLED

    def should_push_fact(self, gate_name: str) -> bool:
        """Check if a gate should push its result to Compass."""
        if self.push_facts is None:
            return False
        policy = self.get_gate_policy(gate_name)
        return policy.push_fact

    def get_gates_with_push_fact(self) -> list[str]:
        """Return list of gate names configured to push facts."""
        return [name for name, policy in self.gates.items() if policy.push_fact]


class OperationalLimits(BaseModel):
    """Configurable limits for operational policy compliance checks.

    Defaults for operational policy checks. Can be overridden via
    certification_policy.operational_limits in metadata.yaml.
    """

    enabled: bool = Field(
        default=False,
        description="Enable operational policy checks. When False, check passes with a warning.",
    )
    max_cpus: int = Field(default=4, gt=0, description="Maximum allowed CPU cores")
    max_memory_mb: int = Field(default=8192, gt=0, description="Maximum allowed memory in MB")
    max_agent_timeout_sec: float = Field(default=3600.0, gt=0, description="Maximum allowed agent timeout in seconds")


class CertificationLevelPolicy(BaseModel):
    """Policy configuration for a single certification level.

    Allows customizing which checks are required and their thresholds.

    Example in metadata.yaml:
        certification_policy:
          foundational:
            checks:
              - valid_skill_structure
              - basic_security_validation
            thresholds:
              basic_execution_validation: 0.5
    """

    checks: list[str] | None = Field(
        default=None,
        description=(
            "List of check IDs required for this level. "
            "When None/omitted, uses the default checks from certification.py. "
            "Check IDs must be valid CheckId enum values."
        ),
    )
    thresholds: dict[str, float] | None = Field(
        default=None,
        description=(
            "Per-check threshold overrides. Keys are check IDs, values are "
            "score thresholds (0.0-1.0). Overrides hardcoded thresholds."
        ),
    )

    @field_validator("thresholds")
    @classmethod
    def _validate_thresholds(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return v
        from abevalflow.certification import CheckId

        valid_check_ids = {c.value for c in CheckId}
        for check_id, threshold in v.items():
            if check_id not in valid_check_ids:
                raise ValueError(
                    f"Invalid threshold key '{check_id}'. Must be a valid CheckId. Valid IDs: {sorted(valid_check_ids)}"
                )
            if not 0.0 <= threshold <= 1.0:
                raise ValueError(f"Threshold for {check_id} must be between 0.0 and 1.0")
        return v


class CertificationPolicy(BaseModel):
    """Policy for configuring certification level requirements.

    Allows customizing which checks are required for each certification level
    and overriding score thresholds. When omitted or partially specified,
    defaults from certification.py are used.

    Example in metadata.yaml:
        certification_policy:
          operational_limits:
            max_cpus: 8
            max_memory_mb: 16384
          foundational:
            checks:
              - valid_skill_structure
              - basic_security_validation
              - basic_execution_validation
              - metadata_compliance
            thresholds:
              basic_execution_validation: 0.5
          trusted:
            checks:
              - evaluation_assets
              - functional_validation
          certified:
            checks:
              - advanced_agent_validation
    """

    foundational: CertificationLevelPolicy | None = Field(
        default=None,
        description="Policy for Foundational certification level",
    )
    trusted: CertificationLevelPolicy | None = Field(
        default=None,
        description="Policy for Trusted certification level",
    )
    certified: CertificationLevelPolicy | None = Field(
        default=None,
        description="Policy for Certified certification level",
    )
    operational_limits: OperationalLimits | None = Field(
        default=None,
        description=(
            "Operational policy limits for resource, timeout, and other checks. "
            "Used by the Trusted-level operational_policy_compliance check. "
            "Overrides OperationalLimits defaults."
        ),
    )

    def get_checks_for_level(self, level: str) -> list[str] | None:
        """Get custom check list for a level, or None to use defaults."""
        level_policy = getattr(self, level, None)
        if level_policy is None:
            return None
        return level_policy.checks

    def get_threshold(self, check_id: str) -> float | None:
        """Get threshold override for a check, or None to use default.

        Searches all level policies for threshold overrides.
        Uses last-wins semantics to match _collect_threshold_overrides behavior:
        if the same check_id is specified in multiple levels, the later level
        (certified > trusted > foundational) takes precedence.
        """
        result: float | None = None
        for level_policy in [self.foundational, self.trusted, self.certified]:
            if level_policy is not None and level_policy.thresholds is not None:
                if check_id in level_policy.thresholds:
                    result = level_policy.thresholds[check_id]
        return result

    def get_operational_limits(self) -> OperationalLimits:
        """Get operational limits for the operational policy compliance check."""
        return self.operational_limits or OperationalLimits()


class ExperimentConfig(BaseModel):
    """Trial count retained for existing catalog metadata; AEH config controls execution."""

    model_config = ConfigDict(extra="forbid")
    n_trials: int = Field(default=20, gt=0, le=100)


class SubmissionMetadata(BaseModel):
    """Schema for metadata.yaml in a submission directory.

    Only 'name' is required. All other fields have sensible defaults so
    that a minimal metadata.yaml can be as simple as:

        name: my-submission
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(
        default=CURRENT_SCHEMA_VERSION,
        description=(
            "Format version of this metadata file. Defaults to the current "
            "version. The pipeline uses this to detect older submissions and "
            "apply any necessary migration or compatibility logic."
        ),
    )

    @field_validator("schema_version")
    @classmethod
    def _validate_schema_version(cls, v: str) -> str:
        if not _SCHEMA_VERSION_RE.fullmatch(v):
            raise ValueError("schema_version must be in 'MAJOR.MINOR' format (e.g. '1.0')")
        return v

    name: str = Field(
        min_length=1, description="Submission name (OCI-safe: lowercase, alphanumeric, hyphens, dots, underscores)"
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _OCI_NAME_RE.fullmatch(v):
            raise ValueError(
                "name must be OCI-safe: lowercase alphanumeric, hyphens, dots, "
                "underscores only, starting with alphanumeric"
            )
        return v

    plugin_name: str | None = Field(
        default=None, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$",
        description="Owning catalog plugin; inferred from <plugin>/skills/<skill> when omitted",
    )
    description: str | None = Field(default=None, description="Brief description of the submission")
    persona: str | None = Field(
        default=None,
        description="Target persona (e.g. rh-sre, rh-developer). Used as category in Harbor.",
    )
    version: str = Field(default="0.1.0", min_length=1, description="Version string")
    author: str | None = Field(default=None, description="Author or team name")
    tags: list[str] | None = Field(default=None, description="Optional classification tags")

    experiment: ExperimentConfig = Field(
        default_factory=ExperimentConfig,
        description=(
            "A/B experiment configuration. Omit entirely to use the default skill experiment with N=20 trials."
        ),
    )

    # Harbor timeout and resource configuration (all optional with defaults)
    agent_timeout_sec: float = Field(default=600.0, gt=0, description="Agent solving timeout")
    cpus: int = Field(default=1, gt=0, description="CPU cores for trial container")
    memory_mb: int = Field(default=2048, gt=0, description="Memory in MB for trial container")

    eval_engine: EvalEngine = Field(default=EvalEngine.AEH, description="AEH evaluation engine")

    aeh_mode: AehMode = Field(
        default=AehMode.SINGLE,
        description=(
            "AEH mode: single or pairwise control/treatment comparison. "
            "The submission setting takes precedence over deployment defaults. "
            "Pairwise mode requires "
            "aeh_control_config / aeh_treatment_config files alongside this "
            "metadata.yaml when set to 'pairwise'."
        ),
    )
    aeh_control_config: str = Field(
        default="eval-control.yaml",
        min_length=1,
        description=(
            "Control-variant AEH config filename (relative to this submission "
            "dir), used only when aeh_mode is 'pairwise'."
        ),
    )
    aeh_treatment_config: str = Field(
        default="eval-treatment.yaml",
        min_length=1,
        description=(
            "Treatment-variant AEH config filename (relative to this "
            "submission dir), used only when aeh_mode is 'pairwise'."
        ),
    )

    security_scan: SecurityScanMode = Field(
        default=SecurityScanMode.WARN,
        description=(
            "Accepted for existing catalog metadata. The shared task always runs "
            "deterministic Tier A scans; gate_policy controls scorecard treatment."
        ),
    )

    skip_quality_review: bool = Field(
        default=False,
        description=("Skip the LLM-based test quality review step. Use when advisory quality review is not needed."),
    )

    gate_policy: GatePolicy | None = Field(
        default=None,
        description=(
            "Optional policy for unified scorecard gate evaluation. "
            "Configures which gates are blocking vs warning, thresholds, "
            "and how results are combined. Defaults are applied when not set."
        ),
    )

    certification_policy: CertificationPolicy | None = Field(
        default=None,
        description=(
            "Optional policy for customizing certification level requirements. "
            "Allows specifying which checks are required for each level and "
            "overriding score thresholds. When not set, default checks and "
            "thresholds from certification.py are used."
        ),
    )
