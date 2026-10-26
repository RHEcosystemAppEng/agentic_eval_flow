"""Policy aggregation services."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import yaml

from abevalflow.certification import compute_certification, load_profile
from abevalflow.engines import get_engine
from abevalflow.gates.base import GateResult
from abevalflow.gates.quality import get_all_quality_gates
from abevalflow.gates.security import get_all_security_gates
from abevalflow.operational_policy import check_operational_policy
from abevalflow.reporting.scorecard import write_certification as write_certification
from abevalflow.reporting.scorecard import write_scorecard as write_scorecard
from abevalflow.schemas import CertificationPolicy, GatePolicy, OperationalLimits, SubmissionMetadata
from abevalflow.scorecard import Scorecard, apply_combination_logic
from abevalflow.skill_structure import load_skill_structure_result

logger = logging.getLogger(__name__)


def load_gate_policy(submission_dir: Path) -> GatePolicy:
    """Load gate policy from metadata.yaml or return defaults."""
    metadata_path = submission_dir / "metadata.yaml"
    if not metadata_path.exists():
        logger.info("No metadata.yaml found, using default policy")
        return GatePolicy()

    try:
        data = yaml.safe_load(metadata_path.read_text())
        if data is None:
            return GatePolicy()

        metadata = SubmissionMetadata(**data)
        if metadata.gate_policy:
            logger.info("Loaded gate policy from metadata.yaml")
            return metadata.gate_policy
        else:
            logger.info("No gate_policy in metadata.yaml, using defaults")
            return GatePolicy()

    except Exception as e:
        logger.warning("Failed to parse metadata.yaml, using defaults: %s", e)
        return GatePolicy()


def _has_eval_assets(submission_dir: Path) -> bool:
    """Whether a submission contains single or pairwise AEH evaluation assets."""
    if (submission_dir / "eval.yaml").exists() or (submission_dir / "cases").is_dir():
        return True

    control_name = "eval-control.yaml"
    treatment_name = "eval-treatment.yaml"
    metadata_path = submission_dir / "metadata.yaml"
    if metadata_path.is_file():
        try:
            data = yaml.safe_load(metadata_path.read_text()) or {}
            if isinstance(data, dict):
                metadata = SubmissionMetadata(**data)
                control_name = metadata.aeh_control_config
                treatment_name = metadata.aeh_treatment_config
        except Exception as e:
            logger.debug("Failed to read aeh_control_config/aeh_treatment_config: %s", e)

    return (submission_dir / control_name).exists() or (submission_dir / treatment_name).exists()


def load_certification_policy(
    submission_dir: Path,
    profile_name: str | None = None,
) -> CertificationPolicy | None:
    """Load certification policy from metadata.yaml or profile.

    Priority:
    1. Submission's metadata.yaml certification_policy (if present)
    2. Profile from config/certification_profiles.yaml (if profile_name provided)
    3. None (uses hardcoded defaults in certification.py)

    Args:
        submission_dir: Path to submission directory
        profile_name: Optional profile name (skill)

    Returns:
        CertificationPolicy or None for defaults
    """
    metadata_path = submission_dir / "metadata.yaml"

    if metadata_path.exists():
        try:
            data = yaml.safe_load(metadata_path.read_text())
            if data:
                metadata = SubmissionMetadata(**data)
                if metadata.certification_policy:
                    logger.info("Loaded certification policy from metadata.yaml")
                    return metadata.certification_policy
        except Exception as e:
            logger.warning("Failed to parse metadata.yaml for certification policy: %s", e)

    if profile_name:
        policy = load_profile(profile_name)
        logger.info("Loaded certification profile '%s'", profile_name)
        return policy

    logger.info("Using default certification policy")
    return None


def load_provenance(reports_dir: Path) -> dict:
    """Load provenance from existing report.json if available."""
    report_path = reports_dir / "report.json"
    if report_path.exists():
        try:
            data = json.loads(report_path.read_text())
            return data.get("provenance", {})
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _extract_behavioral_data(
    reports_dir: Path,
    gates: list[GateResult],
) -> dict | None:
    """Read AEH trial variance for the certification consistency check."""
    behavioral_data: dict = {}

    report_path = reports_dir / "report.json"
    if report_path.exists():
        try:
            report_data = json.loads(report_path.read_text())
            summary = report_data.get("summary", {})
            treatment = summary.get("treatment", {})
            std_reward = treatment.get("std_reward")
            if std_reward is not None:
                behavioral_data["std_reward"] = std_reward
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to parse report.json for behavioral data: %s", e)

    return behavioral_data if behavioral_data else None


def aggregate_scorecard(
    submission_dir: Path,
    results_dir: Path,
    reports_dir: Path,
    workspace_root: Path,
    eval_engine: str,
    pipeline_run_id: str,
    certification_profile: str | None = None,
) -> Scorecard:
    """Aggregate all gates into a unified scorecard.

    Args:
        submission_dir: Path to submissions/{submission-name}/
        results_dir: Path to eval-results/{submission-name}/
        reports_dir: Path to reports/{submission-name}/
        workspace_root: Path to workspace root (for _ai_review.json)
        eval_engine: Evaluation engine (aeh)
        pipeline_run_id: Tekton PipelineRun ID
        certification_profile: Optional profile name (skill).
            If provided and submission has no certification_policy in metadata.yaml,
            the profile's checks are used instead of hardcoded defaults.

    Returns:
        Populated Scorecard instance
    """
    policy = load_gate_policy(submission_dir)
    certification_policy = load_certification_policy(submission_dir, certification_profile)
    provenance = load_provenance(reports_dir)

    submission_name = submission_dir.name
    gates: list[GateResult] = []

    engine_configs = [(eval_engine, reports_dir)]

    for engine_name, engine_reports_dir in engine_configs:
        engine = get_engine(engine_name)
        logger.info("Processing engine gate: %s (reports: %s)", engine.name, engine_reports_dir)

        raw_result = engine.read_result(engine_reports_dir)
        if raw_result:
            engine_gate = engine.to_gate_result(raw_result, policy)
            gates.append(engine_gate)
            logger.info("Engine %s: passed=%s, score=%.3f", engine.name, engine_gate.passed, engine_gate.score)
        else:
            logger.warning("No result found for engine %s at %s", engine.name, engine_reports_dir)

    for security_gate in get_all_security_gates():
        if not policy.is_enabled(security_gate.name):
            logger.info("Security gate %s is disabled, skipping", security_gate.name)
            continue

        logger.info("Processing security gate: %s", security_gate.name)
        gate_result = security_gate.evaluate(reports_dir, policy)
        gates.append(gate_result)
        logger.info(
            "Security %s: passed=%s, score=%.3f, findings=%d",
            security_gate.name,
            gate_result.passed,
            gate_result.score,
            len(gate_result.findings),
        )

    for quality_gate in get_all_quality_gates():
        if not policy.is_enabled(quality_gate.name):
            logger.info("Quality gate %s is disabled, skipping", quality_gate.name)
            continue

        logger.info("Processing quality gate: %s", quality_gate.name)
        gate_result = quality_gate.evaluate(workspace_root, policy)
        gates.append(gate_result)
        logger.info("Quality %s: passed=%s, score=%.3f", quality_gate.name, gate_result.passed, gate_result.score)

    recommendation, reason = apply_combination_logic(gates, policy)
    logger.info("Final recommendation: %s (%s)", recommendation, reason)

    validation_path = reports_dir / "validation.json"
    validation_passed = False
    metadata_valid = False
    validation_errors: list[str] = []

    if validation_path.exists():
        try:
            validation_data = json.loads(validation_path.read_text())
            validation_passed = validation_data.get("valid", False)
            validation_errors = validation_data.get("errors", [])

            metadata_valid = validation_passed
            logger.info(
                "Read validation results: valid=%s, errors=%d",
                validation_passed,
                len(validation_errors),
            )
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to read validation.json: %s", e)
            validation_passed = False
            metadata_valid = False
    else:
        logger.warning(
            "validation.json not found at %s; validation_passed defaults to False",
            validation_path,
        )
        validation_passed = False
        metadata_valid = False

    has_eval_assets = _has_eval_assets(submission_dir)

    operational_limits = certification_policy.get_operational_limits() if certification_policy else OperationalLimits()
    operational_policy_result = check_operational_policy(submission_dir, limits=operational_limits)
    logger.info(
        "Operational policy: passed=%s, score=%.3f",
        operational_policy_result.passed,
        operational_policy_result.score,
    )

    behavioral_data = _extract_behavioral_data(reports_dir, gates)

    certification = compute_certification(
        gates=gates,
        validation_passed=validation_passed,
        skill_structure_result=load_skill_structure_result(reports_dir / "skill-structure-validation.json"),
        metadata_valid=metadata_valid,
        has_eval_assets=has_eval_assets,
        policy=certification_policy,
        operational_policy_result=operational_policy_result,
        behavioral_data=behavioral_data,
    )
    logger.info(
        "Certification levels: foundational=%s, trusted=%s, certified=%s (highest=%s)",
        certification.foundational.passed,
        certification.trusted.passed,
        certification.certified.passed,
        certification.highest_level.value,
    )

    return Scorecard(
        submission_name=submission_name,
        pipeline_run_id=pipeline_run_id,
        eval_engine=eval_engine,
        gates=gates,
        policy=policy,
        recommendation=recommendation,
        recommendation_reason=reason,
        created_at=datetime.now(UTC),
        provenance=provenance,
        certification=certification,
    )
