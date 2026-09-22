#!/usr/bin/env python3
"""Aggregate all gate results into a unified scorecard.

Collects results from the evaluation engine, security gates, and quality gates,
then applies the configured policy to produce a single recommendation.

Note:
    Currently, MCPChecker submissions do not produce a scorecard because the
    analyze task (which runs this script) is skipped for MCPChecker. This is
    a known limitation. To fix it, the scorecard step would need to be
    extracted into a standalone task that runs after evaluate for all engines.

Usage::

    python scripts/aggregate_scorecard.py \\
        --submission-dir /workspace/submissions/my-submission \\
        --results-dir /workspace/eval-results/my-submission \\
        --reports-dir /workspace/reports/my-submission \\
        --workspace-root /workspace \\
        --eval-engine harbor \\
        --pipeline-run-id abc123
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from abevalflow.certification import compute_certification, load_profile
from abevalflow.compass_facts import (
    CertificationFactPushResult,
    FactPushResult,
    UnresolvedEnvVarError,
    push_certification_facts,
    push_gate_fact_from_config,
    validate_push_facts_config,
)
from abevalflow.engines import get_engine
from abevalflow.gates.base import GateResult, GateType
from abevalflow.gates.behavioral import get_all_behavioral_gates
from abevalflow.gates.quality import get_all_quality_gates
from abevalflow.gates.security import get_all_security_gates
from abevalflow.operational_policy import check_operational_policy
from abevalflow.schemas import CertificationPolicy, GatePolicy, OperationalLimits, SubmissionMetadata
from abevalflow.scorecard import Scorecard, apply_combination_logic

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
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
        profile_name: Optional profile name (e.g., 'skill', 'agent')

    Returns:
        CertificationPolicy or None for defaults
    """
    metadata_path = submission_dir / "metadata.yaml"

    # Try loading from metadata.yaml first
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

    # Fall back to profile if specified
    if profile_name:
        # Fail fast if profile is explicitly requested but cannot be loaded
        # (don't silently fall back to defaults on misconfiguration)
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


def _maybe_push_fact(gate_result: GateResult, policy: GatePolicy) -> FactPushResult | None:
    """Push gate fact to Compass if configured.

    Args:
        gate_result: The gate result to potentially push
        policy: Policy containing push_facts configuration

    Returns:
        FactPushResult if push was attempted, None if skipped
    """
    # Policy is keyed by category (gate_name), not implementation (policy_key)
    if not policy.should_push_fact(gate_result.gate_name):
        return None

    if policy.push_facts is None:
        return None

    # Compute intended fact_ref before the call (for use in error case)
    policy_key = gate_result.get_policy_key()
    if policy_key and policy_key != gate_result.gate_name:
        intended_fact_ref = f"{policy.push_facts.fact_ref_prefix}{gate_result.gate_name}_{policy_key}"
    else:
        intended_fact_ref = f"{policy.push_facts.fact_ref_prefix}{gate_result.gate_name}"

    try:
        result = push_gate_fact_from_config(gate_result, policy.push_facts)
        if result.success:
            logger.info("Pushed fact for gate %s to Compass", gate_result.gate_name)
        else:
            logger.warning(
                "Failed to push fact for gate %s: %s",
                gate_result.gate_name,
                result.error,
            )
        return result
    except UnresolvedEnvVarError as e:
        logger.error(
            "Cannot push fact for gate %s: unresolved env var - %s",
            gate_result.gate_name,
            e,
        )
        return FactPushResult(
            gate_name=gate_result.gate_name,
            fact_ref=intended_fact_ref,
            success=False,
            error=str(e),
        )


def _extract_behavioral_data(
    reports_dir: Path,
    gates: list[GateResult],
) -> dict | None:
    """Extract behavioral testing data for certification.

    Assembles behavioral_data from two sources:
    - std_reward: read from report.json (trial variance for consistency check)
    - edge_cases: extracted from the EdgeCaseGate result in gates list
      (single source of truth — avoids re-reading report.json)

    Stability sub-check data is not extracted here because no pipeline
    component currently produces it. The sub-check function in
    certification.py is ready and will activate when upstream producers exist.
    TODO: Add stability extraction here once get_historical_variance()
    in monitor.py is wired into the pipeline.
    """
    behavioral_data: dict = {}

    # Extract trial variance from report.json
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

    # Extract edge case counts from the EdgeCaseGate result
    for gate in gates:
        if gate.gate_type == GateType.BEHAVIORAL and gate.details.get("total") is not None:
            behavioral_data["edge_cases"] = {
                "total": gate.details["total"],
                "passed": gate.details.get("passed", 0),
            }
            break

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
        eval_engine: Primary evaluation engine name (or "both" for Harbor+ASE)
        pipeline_run_id: Tekton PipelineRun ID
        certification_profile: Optional profile name (e.g., 'skill', 'agent').
            If provided and submission has no certification_policy in metadata.yaml,
            the profile's checks are used instead of hardcoded defaults.

    Returns:
        Populated Scorecard instance
    """
    policy = load_gate_policy(submission_dir)
    certification_policy = load_certification_policy(submission_dir, certification_profile)
    provenance = load_provenance(reports_dir)

    # Validate push_facts configuration
    validate_push_facts_config(policy.push_facts, policy.get_gates_with_push_fact())

    submission_name = submission_dir.name
    gates: list[GateResult] = []
    fact_push_results: list[FactPushResult] = []

    # Determine which engines to process and their report directories
    # In 'both' mode, Harbor reports are in reports_dir/harbor/, ASE in reports_dir/
    if eval_engine == "both":
        engine_configs = [
            ("harbor", reports_dir / "harbor"),
            ("ase", reports_dir),
        ]
        logger.info("'both' engine mode: processing Harbor and ASE")
    else:
        engine_configs = [(eval_engine, reports_dir)]

    # Process all requested engines
    for engine_name, engine_reports_dir in engine_configs:
        engine = get_engine(engine_name)
        logger.info("Processing engine gate: %s (reports: %s)", engine.name, engine_reports_dir)

        raw_result = engine.read_result(engine_reports_dir)
        if raw_result:
            engine_gate = engine.to_gate_result(raw_result, policy)
            gates.append(engine_gate)
            push_result = _maybe_push_fact(engine_gate, policy)
            if push_result:
                fact_push_results.append(push_result)
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
        push_result = _maybe_push_fact(gate_result, policy)
        if push_result:
            fact_push_results.append(push_result)
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
        push_result = _maybe_push_fact(gate_result, policy)
        if push_result:
            fact_push_results.append(push_result)
        logger.info("Quality %s: passed=%s, score=%.3f", quality_gate.name, gate_result.passed, gate_result.score)

    for behavioral_gate in get_all_behavioral_gates():
        if not policy.is_enabled(behavioral_gate.name):
            logger.info("Behavioral gate %s is disabled, skipping", behavioral_gate.name)
            continue

        logger.info("Processing behavioral gate: %s", behavioral_gate.name)
        gate_result = behavioral_gate.evaluate(reports_dir, policy)
        gates.append(gate_result)
        push_result = _maybe_push_fact(gate_result, policy)
        if push_result:
            fact_push_results.append(push_result)
        logger.info(
            "Behavioral %s: passed=%s, score=%.3f",
            behavioral_gate.name,
            gate_result.passed,
            gate_result.score,
        )

    # Red team gate: Promptfoo + optional PyRIT Crescendo (combined)
    redteam_results_path = reports_dir / "redteam-results.json"
    pyrit_results_path = reports_dir / "pyrit-crescendo-results.json"
    if redteam_results_path.exists() or pyrit_results_path.exists():
        logger.info(
            "Processing red team gate (promptfoo=%s, pyrit=%s)",
            redteam_results_path.exists(),
            pyrit_results_path.exists(),
        )
        try:
            from abevalflow.gates.base import Finding, Severity

            pf_finding_objects: list[Finding] = []
            pf_total = 0
            pf_num = 0
            if redteam_results_path.exists():
                redteam_data = json.loads(redteam_results_path.read_text())
                results_list = redteam_data.get("results", {}).get("results", [])
                pf_total = len(results_list)
                failed = [
                    r
                    for r in results_list
                    if r.get("gradingResult")
                    and not r["gradingResult"].get("pass", True)
                    and "fetch failed" not in (r["gradingResult"].get("reason") or "")
                ]
                pf_num = len(failed)
                pf_finding_objects = [
                    Finding(
                        severity=Severity.HIGH,
                        message=(f.get("gradingResult", {}).get("reason") or "")[:200],
                        rule_id=f"promptfoo:{f.get('test', {}).get('metadata', {}).get('pluginId', 'unknown')}",
                    )
                    for f in failed[:15]
                ]

            crescendo_finding_objects: list[Finding] = []
            crescendo_total = 0
            crescendo_num = 0
            if pyrit_results_path.exists():
                pyrit_data = json.loads(pyrit_results_path.read_text())
                if not pyrit_data.get("skipped"):
                    cres_results = pyrit_data.get("crescendo_results") or []
                    crescendo_total = len(cres_results)
                    achieved = [r for r in cres_results if r.get("objective_achieved")]
                    crescendo_num = len(achieved)
                    crescendo_finding_objects = [
                        Finding(
                            severity=Severity.HIGH,
                            message=f"{(r.get('objective') or '')[:80]} — {(r.get('judge_reason') or '')[:80]}",
                            rule_id="crescendo",
                        )
                        for r in achieved[:10]
                    ]

            num_findings = pf_num + crescendo_num
            total = pf_total + crescendo_total
            passed = num_findings == 0
            score = 1.0 - (num_findings / max(total, 1)) if total else 1.0
            details_parts = []
            if redteam_results_path.exists():
                details_parts.append(f"promptfoo={pf_num}/{pf_total}")
            if pyrit_results_path.exists() and crescendo_total:
                details_parts.append(f"crescendo={crescendo_num}/{crescendo_total}")

            redteam_gate = GateResult(
                gate_name="security",
                policy_key="red_team",
                gate_type=GateType.SECURITY,
                passed=passed,
                score=round(score, 4),
                details={
                    "promptfoo_findings": pf_num,
                    "promptfoo_total": pf_total,
                    "crescendo_findings": crescendo_num,
                    "crescendo_total": crescendo_total,
                    "total_findings": num_findings,
                    "total_tests": total,
                },
                findings=(pf_finding_objects + crescendo_finding_objects)[:20],
                message=(
                    f"{num_findings} vulnerabilities in {total} adversarial tests "
                    f"({', '.join(details_parts) or 'no tests'})"
                ),
            )
            gates.append(redteam_gate)
            logger.info(
                "Red team: passed=%s, score=%.3f, findings=%d/%d",
                passed,
                score,
                num_findings,
                total,
            )
        except Exception as e:
            logger.warning("Failed to process red team results: %s", e, exc_info=True)

    recommendation, reason = apply_combination_logic(gates, policy)
    logger.info("Final recommendation: %s (%s)", recommendation, reason)

    # Read validation results from validate task output
    validation_path = reports_dir / "validation.json"
    validation_passed = False
    metadata_valid = False
    validation_errors: list[str] = []

    if validation_path.exists():
        try:
            validation_data = json.loads(validation_path.read_text())
            validation_passed = validation_data.get("valid", False)
            validation_errors = validation_data.get("errors", [])
            # Metadata is valid if validation passed (schema check is part of validation)
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

    has_eval_assets = (submission_dir / "evals" / "evals.json").exists() or (submission_dir / "tests").exists()

    operational_limits = certification_policy.get_operational_limits() if certification_policy else OperationalLimits()
    operational_policy_result = check_operational_policy(submission_dir, limits=operational_limits)
    logger.info(
        "Operational policy: passed=%s, score=%.3f",
        operational_policy_result.passed,
        operational_policy_result.score,
    )

    # Extract behavioral data from report + gate results
    behavioral_data = _extract_behavioral_data(reports_dir, gates)

    certification = compute_certification(
        gates=gates,
        validation_passed=validation_passed,
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

    cert_push_results: list[CertificationFactPushResult] = []
    if policy.push_facts is not None:
        try:
            cert_push_results = push_certification_facts(certification, policy.push_facts)
            success_count = sum(1 for r in cert_push_results if r.success)
            logger.info(
                "Certification facts: %d/%d pushed successfully",
                success_count,
                len(cert_push_results),
            )
        except UnresolvedEnvVarError as e:
            logger.error("Cannot push certification facts: unresolved env var - %s", e)

    if fact_push_results:
        success_count = sum(1 for r in fact_push_results if r.success)
        logger.info(
            "Gate facts: %d/%d pushed successfully",
            success_count,
            len(fact_push_results),
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
        fact_push_results=fact_push_results,
        certification=certification,
        certification_fact_push_results=cert_push_results,
    )


def write_scorecard(scorecard: Scorecard, reports_dir: Path) -> Path:
    """Write scorecard to JSON file."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    output_path = reports_dir / "scorecard.json"

    with open(output_path, "w") as f:
        f.write(scorecard.model_dump_json(indent=2))

    logger.info("Wrote scorecard to %s", output_path)
    return output_path


def write_certification(scorecard: Scorecard, reports_dir: Path) -> Path | None:
    """Write standalone certification.json file.

    Creates a separate certification.json alongside scorecard.json for easier
    consumption by systems that only need certification status.

    Returns:
        Path to certification.json, or None if no certification data.
    """
    if scorecard.certification is None:
        logger.info("No certification data to write")
        return None

    reports_dir.mkdir(parents=True, exist_ok=True)
    output_path = reports_dir / "certification.json"

    def _level_data(level_result):
        failed_checks = [c.check_id.value for c in level_result.checks if not c.passed]
        return {
            "passed": level_result.passed,
            "checks_total": level_result.checks_total,
            "checks_passed": level_result.checks_passed,
            "checks": [c.model_dump() for c in level_result.checks],
            "failed_checks": failed_checks,
        }

    cert_data = {
        "submission_name": scorecard.submission_name,
        "pipeline_run_id": scorecard.pipeline_run_id,
        "highest_level": scorecard.highest_certification.value,
        "levels": {
            "foundational": _level_data(scorecard.certification.foundational),
            "trusted": _level_data(scorecard.certification.trusted),
            "certified": _level_data(scorecard.certification.certified),
        },
        "created_at": scorecard.created_at.isoformat(),
    }

    with open(output_path, "w") as f:
        json.dump(cert_data, f, indent=2)

    logger.info("Wrote certification to %s", output_path)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate gate results into unified scorecard")
    parser.add_argument(
        "--submission-dir",
        type=Path,
        required=True,
        help="Path to submissions/{submission-name}/",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help="Path to eval-results/{submission-name}/",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        required=True,
        help="Path to reports/{submission-name}/",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        required=True,
        help="Path to workspace root (for _ai_review.json)",
    )
    parser.add_argument(
        "--eval-engine",
        type=str,
        required=True,
        choices=["harbor", "ase", "a2a", "mcpchecker", "aeh", "aeh_openshell_openclaw", "both"],
        help="Primary evaluation engine",
    )
    parser.add_argument(
        "--pipeline-run-id",
        type=str,
        default="unknown",
        help="Tekton PipelineRun ID",
    )
    parser.add_argument(
        "--output-tekton-results",
        type=Path,
        default=None,
        help="Directory to write Tekton result files",
    )
    parser.add_argument(
        "--certification-profile",
        type=str,
        default=None,
        help="Certification profile name (e.g., 'skill', 'agent', 'mcp_server'). "
        "Provides default checks per artifact type. Overridden by submission's metadata.yaml.",
    )

    args = parser.parse_args()

    from abevalflow.observability.otel import get_tracer, setup_tracer_provider, shutdown_tracer_provider

    setup_tracer_provider()
    tracer = get_tracer("abevalflow.scorecard")

    try:
        with tracer.start_as_current_span(
            "pipeline.aggregate_scorecard",
            attributes={
                "pipeline.run_id": args.pipeline_run_id,
                "pipeline.eval_engine": args.eval_engine,
                "submission.name": args.submission_dir.name,
            },
        ):
            scorecard = aggregate_scorecard(
                submission_dir=args.submission_dir,
                results_dir=args.results_dir,
                reports_dir=args.reports_dir,
                workspace_root=args.workspace_root,
                eval_engine=args.eval_engine,
                pipeline_run_id=args.pipeline_run_id,
                certification_profile=args.certification_profile,
            )

            write_scorecard(scorecard, args.reports_dir)
            write_certification(scorecard, args.reports_dir)

            if args.output_tekton_results:
                results_dir = args.output_tekton_results
                results_dir.mkdir(parents=True, exist_ok=True)

                (results_dir / "scorecard-recommendation").write_text(scorecard.recommendation.value)
                (results_dir / "scorecard-gates-passed").write_text(str(scorecard.gates_passed))
                (results_dir / "scorecard-gates-failed").write_text(str(scorecard.gates_failed))
                (results_dir / "scorecard-blocking-passed").write_text(str(scorecard.blocking_gates_passed))
                (results_dir / "scorecard-blocking-failed").write_text(str(scorecard.blocking_gates_failed))
                (results_dir / "highest-certification").write_text(scorecard.highest_certification.value)

                logger.info("Wrote Tekton results to %s", results_dir)

            print(f"Scorecard recommendation: {scorecard.recommendation.value}")
            print(f"Reason: {scorecard.recommendation_reason}")
            print(f"Gates: {scorecard.gates_passed} passed, {scorecard.gates_failed} failed")
            print(f"Highest certification: {scorecard.highest_certification.value}")

        return 0

    except Exception as e:
        logger.exception("Failed to aggregate scorecard: %s", e)
        return 1
    finally:
        shutdown_tracer_provider()


if __name__ == "__main__":
    sys.exit(main())
