"""Explicit facts consumed by the six Compass AI Skill Validation checks.

Scores come from the same run evidence as latest_skill_scorecards, without
requiring a database connection. Compass owns the pass/fail thresholds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from abevalflow.scorecard_values import check_score, score_100

if TYPE_CHECKING:
    from abevalflow.report import AnalysisResult
    from abevalflow.results.run import RunResult
    from abevalflow.scorecard import Scorecard


SCORE_CONTRACT = "compass-skill-values/v1"
SCORE_COLUMNS = (
    "valid-skill-structure",
    "security_scan_score_100",
    "quality_scan_score_100",
    "behavior_score_100",
)
SCORE_CHECKS = (
    "valid-skill-structure",
    "basic-security-validation",
    "content-quality-review",
    "advanced-security-validation",
    "instruction-quality",
    "enterprise-behavioral-evaluation",
)


def build_skill_values(
    scorecard: Scorecard | None,
    result: RunResult,
    report: AnalysisResult | None,
) -> dict[str, dict[str, Any]]:
    """Canonical observations shared by Compass and signed Component evidence.

    This does not depend on publication configuration or a database connection.
    Availability describes measured evidence, independently of pass thresholds.
    """
    if scorecard and (scorecard.pipeline_run_id != result.run_id or scorecard.submission_name != result.skill):
        raise ValueError("Scorecard belongs to a different run or skill")
    if report and report.submission_name != result.skill:
        raise ValueError("Behavioral report belongs to a different skill")
    if scorecard and scorecard.provenance.get("commit_sha") not in {None, result.source_revision}:
        raise ValueError("Scorecard belongs to a different source revision")
    if report and (
        report.provenance.commit_sha not in {None, result.source_revision}
        or report.provenance.pipeline_run_id not in {None, result.run_id}
    ):
        raise ValueError("Behavioral report belongs to a different run or source revision")
    steps = {step.name: step for step in result.steps if step.tier is not None}

    def scan_score(name: str, implementation: str) -> float | None:
        step = steps.get(name)
        if step is None:
            return None
        gate = next((g for g in scorecard.gates if g.get_policy_key() == implementation), None) if scorecard else None
        return score_100(check_score(step, gate))

    structure = steps.get("structure")
    structure_available = structure is not None and structure.status == "completed"
    valid = bool(structure_available and structure.verdict == "pass")
    security = scan_score("security", "cisco")
    quality = scan_score("quality", "skillmd-quality")
    # The DB stores summary.treatment.mean_reward, even for pairwise evaluations.
    # The evaluation gate score can instead be a pairwise win rate; do not use it.
    behavior = score_100(report.summary.treatment.mean_reward) if report else None
    values = [
        ("valid-skill-structure", valid, "structure", "boolean"),
        ("basic-security-validation", security, "security_scan_score_100", "score_100"),
        ("content-quality-review", quality, "quality_scan_score_100", "score_100"),
        ("advanced-security-validation", security, "security_scan_score_100", "score_100"),
        # No separate instruction evaluator exists: this is the quality scan,
        # evaluated against the instruction check's own threshold in Compass.
        ("instruction-quality", quality, "quality_scan_score_100", "score_100"),
        ("enterprise-behavioral-evaluation", behavior, "behavior_score_100", "score_100"),
    ]
    observations = {}
    for check_id, value, source, value_type in values:
        data = {
            "schema_version": "1.0",
            "check_id": check_id,
            "value": value,
            "value_type": value_type,
            "available": structure_available if check_id == "valid-skill-structure" else value is not None,
            "source": source,
            "run_id": result.run_id,
            "source_revision": result.source_revision,
            "evaluated_at": (
                scorecard.created_at if scorecard else result.completed_at or result.started_at
            ).isoformat(),
        }
        observations[check_id] = data
    return observations


def build_skill_facts(
    scorecard: Scorecard,
    result: RunResult,
    report: AnalysisResult | None,
) -> list[dict[str, Any]]:
    """Publish all six identities, including unavailable values that clear stale facts."""
    config = scorecard.policy.push_facts
    if config is None:
        return []
    values = build_skill_values(scorecard, result, report)
    structure = next((s for s in result.steps if s.name == "structure"), None)
    facts = []
    for check_id, data in values.items():
        if check_id == "valid-skill-structure":
            # Retain the former standalone structure fact's fields for consumers.
            data = {**(structure.details if structure else {}), **data}
            data["check_id"] = "valid_skill_structure"
            data["passed"] = data["value"]
        facts.append({"factRef": config.fact_ref_prefix + check_id, "entityRef": config.entity_ref, "data": data})
    return facts


def compact_skill_scores(evaluations: list[dict]) -> dict:
    """Four measured values represent the six checks without duplicating scores.

    Null is an unavailable observation, never zero. Detailed records retain the
    exact Compass value/available fields and per-run provenance in the archive.
    """
    primary = (SCORE_CHECKS[0], SCORE_CHECKS[1], SCORE_CHECKS[2], SCORE_CHECKS[5])
    return {
        "score_contract": SCORE_CONTRACT,
        "score_columns": list(SCORE_COLUMNS),
        "skill_scores": {
            evaluation["skill_id"]: [
                values[check]["value"] if values[check]["available"] else None for check in primary
            ]
            for evaluation in evaluations
            for values in [evaluation["skill_facts"]]
        },
    }
