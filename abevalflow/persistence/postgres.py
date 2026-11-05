"""Transactional, retry-safe projection of pipeline evidence into schema v2."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from abevalflow.aeh_scoring import _judge_errored, _normalize_numeric_value
from abevalflow.config.runtime import RuntimeConfig
from abevalflow.db.engine import get_engine, init_db, make_session
from abevalflow.db.models import (
    CaseResult,
    CertificationResult,
    CheckResult,
    CheckScore,
    EvaluationMetrics,
    EvaluationVariant,
    EvidenceArtifact,
    Finding,
    JudgeResult,
    JudgeSummary,
    PairwiseCaseResult,
    PairwiseResult,
    Plugin,
    Skill,
    SkillEvaluation,
    TierResult,
)
from abevalflow.observability.context import MetricsContext
from abevalflow.report import AnalysisResult
from abevalflow.results.run import RunResult, StepResult
from abevalflow.scorecard import Scorecard
from abevalflow.scorecard_values import check_score

TOOLS = {"security": "cisco", "quality": "skillmd-quality", "submission-review": "llm-review", "evaluate": "aeh"}


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(value) else None


def _get_or_create(session: Session, model, defaults: dict | None = None, **identity):
    """Use a savepoint so concurrent plugin/skill/run inserts do not abort the snapshot."""
    query = select(model).filter_by(**identity)
    existing = session.scalar(query)
    if existing is not None:
        return existing
    try:
        with session.begin_nested():
            row = model(**identity, **(defaults or {}))
            session.add(row)
            session.flush()
        return row
    except IntegrityError:
        existing = session.scalar(query)
        if existing is None:
            raise
        return existing


def _verdict(steps: list[StepResult]) -> str:
    if not steps or any(s.status != "completed" for s in steps):
        return "unavailable"
    return next((v for v in ("fail", "warn", "unavailable") if any(s.verdict == v for s in steps)), "pass")


def _checks(config: RuntimeConfig, result: RunResult, scorecard: Scorecard | None) -> list[TierResult]:
    requested = {s for s in result.requested_steps if s not in {"prepare", "report"}}
    recorded = {s.name: s for s in result.steps if s.tier is not None}
    steps = list(recorded.values())
    for name in sorted(requested - recorded.keys()):
        steps.append(
            StepResult(
                name=name,
                tier=1 if name in {"structure", "security", "quality", "skill-review"} else 2,
                status="skipped",
                verdict="unavailable",
                reason="No phase evidence recorded",
            )
        )
    tiers = []
    for tier in sorted(set(config.tiers) | {s.tier for s in steps}):
        selected = [s for s in steps if s.tier == tier]
        status = (
            "error"
            if any(s.status == "error" for s in selected)
            else ("completed" if selected and all(s.status == "completed" for s in selected) else "partial")
        )
        row = TierResult(tier=tier, status=status, verdict=_verdict(selected))
        for step in selected:
            gate = (
                next((g for g in scorecard.gates if g.get_policy_key() == TOOLS.get(step.name, step.name)), None)
                if scorecard
                else None
            )
            details = {**step.details, **(gate.details if gate else {})}
            findings = [f.model_dump(mode="json") for f in gate.findings] if gate else details.get("findings", [])
            dimensions = details.get("dimensions", {})
            for key in ("findings", "dimensions", "judges", "per_case", "treatment", "control", "pairwise"):
                details.pop(key, None)
            category = (
                gate.gate_name
                if gate
                else (
                    "evaluation"
                    if step.name == "evaluate"
                    else "security"
                    if step.name == "security"
                    else "validation"
                    if step.name in {"structure", "submission"}
                    else "quality"
                )
            )
            scoring_details = details.get("scoring") or {}
            score_method = (
                scoring_details.get("method", "severity_penalty_v1")
                if step.name in {"security", "quality"}
                else (
                    (gate.details.get("score_method") if gate else None)
                    or (
                        "pairwise_win_rate"
                        if config.metadata and config.metadata.aeh_mode.value == "pairwise"
                        else "mean_reward"
                    )
                    if step.name == "evaluate"
                    else "llm_review_overall_score"
                    if step.name == "submission-review"
                    else "tool_gate_score"
                )
            )
            check = CheckResult(
                name=step.name,
                category=category,
                tool=TOOLS.get(step.name, step.name),
                status=step.status,
                verdict=step.verdict,
                blocking=step.blocking,
                score=check_score(step, gate),
                score_method=score_method,
                passed=gate.passed if gate else None,
                evaluated_at=gate.evaluated_at if gate else None,
                enforcement_mode=gate.mode.value if gate else None,
                threshold=gate.threshold if gate else None,
                message=gate.message if gate else step.reason,
                details=details,
            )
            for f in findings:
                check.findings.append(
                    Finding(
                        severity=str(f.get("severity", "info")).lower(),
                        rule_id=f.get("rule_id") or f.get("id"),
                        message=str(f.get("message") or f.get("description") or f.get("title") or "Finding"),
                        location=f.get("location") or f.get("file_path"),
                        case_key=f.get("case_id") or (f.get("details") or {}).get("case_id"),
                        details=f.get("details", f),
                    )
                )
            if isinstance(dimensions, dict):
                check.components = [
                    CheckScore(
                        name=str(name),
                        score=_number(data.get("score")) if isinstance(data, dict) else _number(data),
                        details=data,
                    )
                    for name, data in dimensions.items()
                ]
            row.checks.append(check)
        tiers.append(row)
    return tiers


def _report_scans(report: AnalysisResult, tiers: list[TierResult]) -> None:
    """Retain explicit scan reports when a matching workflow gate did not carry their findings."""
    for scan in report.security_scans:
        tier = next((t for t in tiers if t.tier == 1), None)
        if tier is None:
            tier = TierResult(tier=1, status="completed", verdict="pass")
            tiers.append(tier)
        check = next((c for c in tier.checks if c.category == "security" and c.tool == scan.scanner), None)
        if check is None:
            check = CheckResult(
                name=f"security:{scan.scanner}",
                category="security",
                tool=scan.scanner,
                status="completed",
                verdict="pass" if scan.passed else "fail",
                passed=scan.passed,
                blocking=scan.scan_mode.value == "block",
                enforcement_mode=scan.scan_mode.value,
                details=scan.model_dump(mode="json", exclude={"findings", "severity_counts"}),
            )
            tier.checks.append(check)
            if not scan.passed:
                tier.verdict = "fail"
        if not check.findings:
            check.findings = [
                Finding(
                    severity=f.severity.value,
                    rule_id=f.rule_id,
                    message=f.message,
                    location=f.file_path,
                    details=f.model_dump(mode="json"),
                )
                for f in scan.findings
            ]


def _variants(report: AnalysisResult) -> list[EvaluationVariant]:
    variants = []
    for name in ("treatment", "control"):
        arm = getattr(report, name) or {}
        trials = report.trials.get(name, [])
        summary = getattr(report.summary, name)
        if name == "control" and report.mode != "pairwise" and not trials and not summary.n_trials:
            continue
        judges = arm.get("judges") or (report.judges if name == "treatment" else {}) or {}
        scoring = arm.get("scoring") or report.scoring
        row = EvaluationVariant(
            name=name,
            **summary.model_dump(),
            scoring=scoring,
            execution=arm.get("execution") or (report.execution if name == "treatment" else None),
            run_metrics=arm.get("run_metrics"),
        )
        for key, data in judges.items():
            d = data if isinstance(data, dict) else {}
            row.judges.append(
                JudgeSummary(
                    name=str(key),
                    mean=_number(d.get("mean", data)),
                    pass_rate=_number(d.get("pass_rate")),
                    errored_cases=d.get("errored_cases"),
                    details=data,
                )
            )
        per_case = arm.get("per_case") or (report.per_case if name == "treatment" else {}) or {}
        trial_map = {t.trial_name: t for t in trials}
        for key in sorted(set(per_case) | set(trial_map)):
            trial = trial_map.get(key)
            payload = per_case.get(key, {})
            case = CaseResult(
                case_key=key,
                reward=trial.reward if trial else None,
                passed=trial.passed if trial and trial.reward is not None else None,
                status="completed" if trial and trial.reward is not None else "unavailable",
            )
            values = trial.judges if trial and trial.judges else payload
            if not isinstance(values, dict):
                values = {}
            for judge, data in values.items():
                if judge in {"reward", "mean_reward"}:
                    continue
                d = data if isinstance(data, dict) else {"value": data}
                value = d.get("value")
                ranges = scoring.get("score_ranges", {})
                score_range = ranges.get(judge)
                normalized = None
                if not _judge_errored(d) and _number(value) is not None:
                    normalized = _normalize_numeric_value(value, tuple(score_range) if score_range else None)
                case.judges.append(
                    JudgeResult(
                        name=str(judge),
                        judge_type=d.get("judge_type"),
                        raw_value=value,
                        normalized_score=normalized,
                        score_range=score_range,
                        error=d.get("error"),
                        details={k: v for k, v in d.items() if k not in {"value", "judge_type", "error"}},
                    )
                )
            row.cases.append(case)
        variants.append(row)
    return variants


def _pairwise(report: AnalysisResult, variants: list[EvaluationVariant]) -> PairwiseResult | None:
    p = report.pairwise
    if p is None:
        return None
    row = PairwiseResult(
        run_a=p.run_a,
        run_b=p.run_b,
        cases_compared=p.cases_compared,
        treatment_wins=p.wins_a,
        control_wins=p.wins_b,
        ties=p.ties,
        errors=p.errors,
        win_rate=p.win_rate,
        stability=p.stability,
    )
    cases = {(v.name, c.case_key): c.id for v in variants for c in v.cases}
    for index, data in enumerate(p.per_case):
        key = str(data.get("case_id") or data.get("case_key") or f"unknown-{index}")
        winner = str(data.get("winner", "")).lower()
        outcome = (
            "error"
            if data.get("error")
            else {
                "a": "treatment",
                "b": "control",
                "tie": "tie",
                "error": "error",
            }.get(winner, "unavailable")
        )
        row.cases.append(
            PairwiseCaseResult(
                case_key=key,
                outcome=outcome,
                error=data.get("error"),
                details=data,
                treatment_case_id=cases.get(("treatment", key)),
                control_case_id=cases.get(("control", key)),
            )
        )
    return row


def store_snapshot(config: RuntimeConfig, result: RunResult, database_url: str) -> None:
    """Project one run atomically; retries preserve the evaluation UUID and execution time."""
    if not config.plugin:
        raise ValueError("Postgres requires a catalog plugin or --plugin-name")
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", result.source_revision):
        raise ValueError("Postgres requires the evaluated commit SHA in --revision")
    if result.run_id != config.run_id or result.source_revision != config.revision:
        raise ValueError("Run identity does not match its recorded configuration")
    reports = config.output / "reports"
    scorecard_path, report_path = reports / "scorecard.json", reports / "report.json"
    scorecard = Scorecard.model_validate_json(scorecard_path.read_text()) if scorecard_path.is_file() else None
    report = AnalysisResult.model_validate_json(report_path.read_text()) if report_path.is_file() else None
    for payload in (scorecard, report):
        if payload and payload.submission_name != config.name:
            raise ValueError("Report belongs to a different submission")
    engine = get_engine(database_url)
    try:
        init_db(engine)
        with make_session(engine).begin() as session:
            plugin = _get_or_create(session, Plugin, name=config.plugin)
            skill = _get_or_create(session, Skill, plugin_id=plugin.id, name=config.skill.name)
            _get_or_create(
                session,
                SkillEvaluation,
                run_id=result.run_id,
                defaults={
                    "skill_id": skill.id,
                    "submission_name": config.name,
                    "commit_sha": result.source_revision,
                    "evaluator_version": result.evaluator_version,
                    "started_at": result.started_at,
                    "status": "partial",
                    "evidence_complete": False,
                },
            )
            query = select(SkillEvaluation).where(SkillEvaluation.run_id == result.run_id).with_for_update()
            row = session.scalar(query)
            if row.skill_id != skill.id or row.commit_sha != result.source_revision:
                raise ValueError("Run ID already belongs to a different skill or revision")
            if row.built_image_ref and row.built_image_ref != config.built_image_ref:
                raise ValueError("Run ID already refers to a different built image")
            for provenance in (
                report.provenance.model_dump() if report else {},
                scorecard.provenance if scorecard else {},
            ):
                if provenance.get("commit_sha") and provenance["commit_sha"] != result.source_revision:
                    raise ValueError("Report commit SHA does not match the evaluated revision")
            row.eval_engine = scorecard.eval_engine if scorecard else report.provenance.eval_engine if report else "aeh"
            row.built_image_ref = config.built_image_ref
            row.trial_image_ref = config.trial_image
            row.source_digest = next(
                (s.details.get("source_digest") for s in result.steps if s.name == "prepare"), None
            )
            row.mode = report.mode if report else config.metadata.aeh_mode.value if config.metadata else None
            row.completed_at = result.completed_at
            row.evidence_complete = result.evidence_complete
            row.status = (
                "error"
                if any(s.status == "error" for s in result.steps)
                else ("completed" if result.evidence_complete else "partial")
            )
            row.recommendation = scorecard.recommendation.value if scorecard else None
            row.recommendation_reason = scorecard.recommendation_reason if scorecard else None
            row.policy_json = json.loads(scorecard.policy.model_dump_json()) if scorecard else None
            row.requested_steps = result.requested_steps
            row.execution_details = {s.name: s.model_dump(mode="json") for s in result.steps if s.tier is None}
            row.comparison_statistics = None
            row.warnings = []
            if report:
                row.comparison_statistics = report.summary.model_dump(mode="json", exclude={"treatment", "control"})
                row.warnings = report.aeh_warnings
            # Delete through the ORM to enforce cascades in both PostgreSQL and SQLite.
            row.pairwise = None
            session.flush()
            row.variants.clear()
            row.tiers.clear()
            row.certifications.clear()
            row.artifacts.clear()
            row.metrics = None
            session.flush()
            row.tiers = _checks(config, result, scorecard)
            if report:
                _report_scans(report, row.tiers)
            if report:
                row.variants = _variants(report)
                session.flush()  # Case UUIDs are needed to link the pairwise comparisons to both arms.
                row.pairwise = _pairwise(report, row.variants)
            if scorecard and scorecard.certification:
                for level in (
                    scorecard.certification.foundational,
                    scorecard.certification.trusted,
                    scorecard.certification.certified,
                ):
                    row.certifications.append(
                        CertificationResult(
                            level=level.level.value,
                            passed=level.passed,
                            checks_total=level.checks_total,
                            checks_passed=level.checks_passed,
                            checks=[c.model_dump(mode="json") for c in level.checks],
                        )
                    )
            objects = {}
            for outcome in result.publication:
                for artifact in outcome.details.get("objects", []):
                    objects[artifact["uri"]] = artifact
            row.artifacts = [EvidenceArtifact(**artifact) for artifact in objects.values()]
            metrics = MetricsContext.load_checkpoint(reports)
            if metrics:
                values = metrics.to_observability_dict()
                values.pop("submission_name", None)
                row.metrics = EvaluationMetrics(
                    **values,
                    phase_metrics=metrics.model_dump(mode="json", include={"timings", "token_usage"}),
                )
    finally:
        engine.dispose()
