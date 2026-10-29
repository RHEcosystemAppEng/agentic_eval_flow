"""Generate and validate Compass SoundCheck fact data files from MCP
evaluation phase summaries.

Top-to-bottom flow:
  1. Phase config — check names, consumed checks, source defaults
  2. Domain models — McpCheckResult, McpPhaseResult, McpEvalResult
  3. Summary loader — load_phase_summary, build_mcp_eval_result
  4. Schema validation — validate against JSON Schema files in schemas/
  5. Fact-ref mappings — which checks map to which Compass fact refs
  6. Fact builders + file generator — transform models into fact data files

No Compass interaction — this is a pure transformation and validation
module.  The publishing Job injects entityRef from the Compass secret
before POSTing.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema
from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 1. Phase-specific interpretation config
# ---------------------------------------------------------------------------

PHASE_CHECK_NAMES: dict[str, dict[str, str]] = {
    "static-eval": {
        "mcp-secrets": "Secrets Management",
        "mcp-no-user-code": "No User-Code Execution",
        "mcp-license": "License Compliance",
    },
    "conformance-eval": {
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
    "behavioral-eval": {
        "tool-correctness": "Tool Correctness",
        "edge-case-handling": "Edge Case Handling",
        "sensitive-data-redaction": "Sensitive Data Redaction",
    },
}

PHASE_CONSUMED_CHECKS: dict[str, set[str]] = {
    "conformance-eval": {"tool-name-rules", "oauth-catalog-match"},
}

PHASE_SOURCE_DEFAULTS: dict[str, str] = {
    "static-eval": "pipeline",
    "conformance-eval": "probe",
    "behavioral-eval": "pipeline",
}

PHASE_CHECK_SOURCE_DEFAULTS: dict[str, dict[str, str]] = {
    "conformance-eval": {
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
# 2. Domain models
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

        def score_or_default(check: McpCheckResult) -> float:
            if check.score is not None:
                return check.score
            return 1.0 if check.status == "pass" else 0.0

        return sum(score_or_default(c) for c in evaluated) / len(evaluated)


class McpEvalResult(BaseModel):
    """Complete MCP evaluation result across any supplied phases."""

    phases: dict[str, McpPhaseResult]

    @model_validator(mode="after")
    def phases_are_nonempty(self) -> McpEvalResult:
        if not self.phases:
            raise ValueError("at least one phase result is required")
        return self


# ---------------------------------------------------------------------------
# 3. Summary loader
# ---------------------------------------------------------------------------


def load_phase_summary(path: Path, *, validate: bool = True) -> McpPhaseResult:
    """Load a standardized phase summary JSON file into a domain model.

    When *validate* is True (default), the file is validated against the
    phase summary JSON Schema before loading.  The parsed dict is reused
    — the file is read only once.
    """
    data = json.loads(path.read_text())
    if validate:
        jsonschema.validate(data, _get_summary_schema())
    phase_name = data["phase"]
    checks: list[McpCheckResult] = []

    for check in data.get("checks", []):
        check_id = check["check"]
        status = check.get("status")
        if status is None:
            status = "pass" if check["passed"] else "fail"
        if status not in VALID_CHECK_STATUSES:
            raise ValueError(f"Invalid status {status!r} for check {check_id!r} in phase {phase_name!r}")

        passed = True if status == "pass" else False if status == "fail" else None

        default_score = 1.0 if status == "pass" else 0.0
        source = check.get("source")
        if not source:
            source = PHASE_CHECK_SOURCE_DEFAULTS.get(phase_name, {}).get(
                check_id,
                PHASE_SOURCE_DEFAULTS.get(phase_name, DEFAULT_SOURCE),
            )
        
        checks.append(
            McpCheckResult(
                check_id=check_id,
                name=PHASE_CHECK_NAMES.get(phase_name, {}).get(check_id, check_id),
                passed=passed,
                score=None if status == "not_evaluated" else check.get("score", default_score),
                status=status,
                message=check.get("message", ""),
                source=source,
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


# ---------------------------------------------------------------------------
# 4. Schema validation
# ---------------------------------------------------------------------------

_SCHEMAS_DIR = Path(__file__).resolve().parent.parent.parent / "schemas"

_SUMMARY_SCHEMA: dict | None = None
_FACT_SCHEMA: dict | None = None


def _get_summary_schema() -> dict:
    global _SUMMARY_SCHEMA
    if _SUMMARY_SCHEMA is None:
        _SUMMARY_SCHEMA = json.loads((_SCHEMAS_DIR / "mcp_eval_report_schema.json").read_text())
    return _SUMMARY_SCHEMA


def _get_fact_schema() -> dict:
    global _FACT_SCHEMA
    if _FACT_SCHEMA is None:
        _FACT_SCHEMA = json.loads((_SCHEMAS_DIR / "compass_fact_schema.json").read_text())
    return _FACT_SCHEMA


def validate_compass_fact_file(path: Path) -> dict:
    """Validate a fact data file against the compass fact JSON Schema.

    Returns the parsed data on success.  Raises ``jsonschema.ValidationError``
    on schema violations.
    """
    data = json.loads(path.read_text())
    jsonschema.validate(data, _get_fact_schema())
    return data


# ---------------------------------------------------------------------------
# 5. Fact-ref mapping tables
# ---------------------------------------------------------------------------

PHASE1_FACT_MAP: dict[str, str] = {
    "mcp-secrets": "abevalflow:default/mcp_secrets",
    "mcp-no-user-code": "abevalflow:default/mcp_no_user_code",
    "mcp-license": "abevalflow:default/mcp_license",
}

PHASE2_FACT_MAP: dict[str, str] = {
    "http-support": "abevalflow:default/mcp_http_support",
    "protocol-compliance": "abevalflow:default/mcp_protocol_compliance",
    "schema-conformance": "abevalflow:default/mcp_schema_conformance",
    "tool-annotations": "abevalflow:default/mcp_tool_annotations",
    "rate-limiting": "abevalflow:default/mcp_rate_limiting",
    "response-size-limit": "abevalflow:default/mcp_response_size_limit",
    "mandatory-timeouts": "abevalflow:default/mcp_mandatory_timeouts",
    "openapi-conformance": "abevalflow:default/mcp_openapi_conformance",
}

PHASE3_FACT_MAP: dict[str, str] = {
    "tool-correctness": "abevalflow:default/mcp_tool_correctness",
    "edge-case-handling": "abevalflow:default/mcp_edge_case_handling",
    "sensitive-data-redaction": "abevalflow:default/mcp_sensitive_data_redaction",
}

PHASE_FACT_MAPS: dict[str, dict[str, str]] = {
    "static-eval": PHASE1_FACT_MAP,
    "conformance-eval": PHASE2_FACT_MAP,
    "behavioral-eval": PHASE3_FACT_MAP,
}

PHASE_SUMMARY_FACTS: dict[str, str] = {
    "static-eval": "abevalflow:default/mcp_phase1",
    "conformance-eval": "abevalflow:default/mcp_phase2",
    "behavioral-eval": "abevalflow:default/mcp_phase3",
}

MCP_EVAL_SUMMARY_FACT = "abevalflow:default/mcp_eval"

# ---------------------------------------------------------------------------
# 6. Fact builders + file generator
# ---------------------------------------------------------------------------


def _sanitize_fact_ref(fact_ref: str) -> str:
    """Turn a factRef into a safe filename stem."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", fact_ref)


def _write_fact_file(fact_data: dict[str, Any], fact_ref: str, output_dir: Path) -> Path:
    """Write a fact data file and return its path."""
    path = output_dir / f"{_sanitize_fact_ref(fact_ref)}.json"
    path.write_text(json.dumps(fact_data, indent=2))
    return path


def _build_check_fact_data(
    check: McpCheckResult,
    fact_ref: str,
    evaluated_at: datetime,
) -> dict[str, Any]:
    return {
        "factRef": fact_ref,
        "data": {
            "passed": check.passed,
            "score": check.score,
            "status": check.status,
            "message": check.message,
            "source": check.source,
            "evaluated_at": evaluated_at.isoformat(),
        },
    }


def _build_phase_summary_fact_data(
    phase_result: McpPhaseResult,
    fact_ref: str,
    evaluated_at: datetime,
) -> dict[str, Any]:
    checks_data = [
        {
            "check_id": c.check_id,
            "name": c.name,
            "passed": c.passed,
            "score": c.score,
            "status": c.status,
            "source": c.source,
        }
        for c in phase_result.checks
    ]
    return {
        "factRef": fact_ref,
        "data": {
            "phase": phase_result.phase,
            "passed": phase_result.passed,
            "checks_passed": phase_result.checks_passed,
            "checks_evaluated": phase_result.checks_evaluated,
            "checks_total": phase_result.checks_total,
            "overall_score": phase_result.overall_score,
            "checks": checks_data,
            "evaluated_at": evaluated_at.isoformat(),
        },
    }


def _build_eval_summary_fact_data(
    eval_result: McpEvalResult,
    fact_ref: str,
    evaluated_at: datetime,
) -> dict[str, Any]:
    return {
        "factRef": fact_ref,
        "data": {
            "phases": {
                phase_name: {
                    "passed": phase.passed,
                    "score": phase.overall_score,
                    "checks_evaluated": phase.checks_evaluated,
                    "checks_total": phase.checks_total,
                }
                for phase_name, phase in eval_result.phases.items()
            },
            "evaluated_at": evaluated_at.isoformat(),
        },
    }


def generate_mcp_eval_fact_files(
    eval_result: McpEvalResult,
    output_dir: Path,
) -> list[Path]:
    """Generate fact data files without publishing.

    Each file contains ``{"factRef": "...", "data": {...}}`` — the
    evaluation data keyed by fact reference.  The delivery-specific
    ``entityRef`` is NOT included; the publishing step injects it from
    the Compass secret before POSTing.

    Returns the list of written file paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    evaluated_at = datetime.now(UTC)
    written: list[Path] = []

    for phase_name, phase_result in eval_result.phases.items():
        # Generate per-check facts
        fact_map = PHASE_FACT_MAPS.get(phase_name, {})
        consumed_checks = PHASE_CONSUMED_CHECKS.get(phase_name, set())
        for check in phase_result.checks:
            if check.status == "not_evaluated":
                logger.info("Skipping not-evaluated check %s", check.check_id)
                continue
            if check.check_id in consumed_checks:
                logger.debug("Skipping consumed check %s", check.check_id)
                continue
            check_fact_ref = fact_map.get(check.check_id)
            if check_fact_ref is None:
                logger.warning("No factRef mapping for check %s; skipping", check.check_id)
                continue
            fact_data = _build_check_fact_data(check, check_fact_ref, evaluated_at)
            written.append(_write_fact_file(fact_data, check_fact_ref, output_dir))

        # Generate phase summary fact
        phase_fact_ref = PHASE_SUMMARY_FACTS.get(phase_name)
        if phase_fact_ref is None:
            logger.warning("No phase summary factRef mapping for phase %s; skipping", phase_name)
            continue
        fact_data = _build_phase_summary_fact_data(phase_result, phase_fact_ref, evaluated_at)
        written.append(_write_fact_file(fact_data, phase_fact_ref, output_dir))

    summary_data = _build_eval_summary_fact_data(eval_result, MCP_EVAL_SUMMARY_FACT, evaluated_at)
    written.append(_write_fact_file(summary_data, MCP_EVAL_SUMMARY_FACT, output_dir))

    logger.info("Generated %d fact file(s) in %s", len(written), output_dir)
    return written
