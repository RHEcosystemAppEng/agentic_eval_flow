"""Persistence postgres services."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from abevalflow.db.engine import get_engine, init_db, make_session
from abevalflow.db.models import (
    CertificationRow,
    EvaluationRun,
    GateResultRow,
    ObservabilityMetricsRow,
    ScorecardRow,
    SecurityScan,
    Trial,
)
from abevalflow.observability.context import MetricsContext
from abevalflow.report import AnalysisResult
from abevalflow.scorecard import Scorecard


def map_result_to_run(result: AnalysisResult, run_id: str) -> EvaluationRun:
    """Flatten an AnalysisResult into an EvaluationRun row."""
    s = result.summary
    p = result.provenance
    t = s.treatment
    c = s.control

    return EvaluationRun(
        submission_name=result.submission_name,
        pipeline_run_id=run_id,
        commit_sha=p.commit_sha,
        treatment_image_ref=p.treatment_image_ref,
        control_image_ref=p.control_image_ref,
        harbor_fork_revision=p.harbor_fork_revision,
        eval_engine=p.eval_engine,
        recommendation=s.recommendation.value,
        uplift=s.uplift,
        mean_reward_gap=s.mean_reward_gap,
        ttest_p_value=s.ttest_p_value,
        fisher_p_value=s.fisher_p_value,
        treatment_n_trials=t.n_trials,
        treatment_n_passed=t.n_passed,
        treatment_n_failed=t.n_failed,
        treatment_n_errors=t.n_errors,
        treatment_pass_rate=t.pass_rate,
        treatment_mean_reward=t.mean_reward,
        treatment_median_reward=t.median_reward,
        treatment_std_reward=t.std_reward,
        control_n_trials=c.n_trials,
        control_n_passed=c.n_passed,
        control_n_failed=c.n_failed,
        control_n_errors=c.n_errors,
        control_pass_rate=c.pass_rate,
        control_mean_reward=c.mean_reward,
        control_median_reward=c.median_reward,
        control_std_reward=c.std_reward,
        report_json=json.loads(result.model_dump_json()),
    )


def map_trials(result: AnalysisResult, run: EvaluationRun) -> list[Trial]:
    """Create Trial rows from the per-variant trial lists."""
    trials: list[Trial] = []
    for variant, trial_list in result.trials.items():
        for tr in trial_list:
            dumped = tr.model_dump()
            trials.append(
                Trial(
                    run=run,
                    variant=variant,
                    trial_name=dumped["trial_name"],
                    reward=dumped["reward"],
                    passed=dumped["passed"],
                )
            )
    return trials


def map_security_scans(
    result: AnalysisResult,
    run_id: str,
) -> list[SecurityScan]:
    """Create SecurityScan rows from security_scans in the report."""
    scans: list[SecurityScan] = []
    for scan in result.security_scans:
        findings_list = [f.model_dump() for f in scan.findings]
        scans.append(
            SecurityScan(
                pipeline_run_id=run_id,
                submission_name=result.submission_name,
                scanner=scan.scanner,
                scan_mode=scan.scan_mode.value,
                passed=scan.passed,
                findings_count=len(scan.findings),
                critical_count=scan.severity_counts.get("critical", 0),
                high_count=scan.severity_counts.get("high", 0),
                findings_json=findings_list,
            )
        )
    return scans


def map_scorecard_to_row(scorecard: Scorecard) -> ScorecardRow:
    """Flatten a Scorecard Pydantic model into a ScorecardRow."""
    return ScorecardRow(
        pipeline_run_id=scorecard.pipeline_run_id,
        submission_name=scorecard.submission_name,
        eval_engine=scorecard.eval_engine,
        recommendation=scorecard.recommendation.value,
        recommendation_reason=scorecard.recommendation_reason,
        combination_mode=scorecard.policy.combination.value,
        gates_passed=scorecard.gates_passed,
        gates_failed=scorecard.gates_failed,
        blocking_gates_passed=scorecard.blocking_gates_passed,
        blocking_gates_failed=scorecard.blocking_gates_failed,
        highest_certification=scorecard.highest_certification.value,
        scorecard_json=json.loads(scorecard.model_dump_json()),
    )


def map_gate_results(scorecard: Scorecard, scorecard_row: ScorecardRow) -> list[GateResultRow]:
    """Create GateResultRow rows from scorecard gates."""
    rows: list[GateResultRow] = []
    for gate in scorecard.gates:
        rows.append(
            GateResultRow(
                scorecard=scorecard_row,
                gate_name=gate.gate_name,
                gate_type=gate.gate_type.value,
                policy_key=gate.get_policy_key(),
                passed=gate.passed,
                score=gate.score,
                mode=gate.mode.value,
                threshold=gate.threshold,
                findings_count=len(gate.findings),
                message=gate.message,
                details_json=gate.details if gate.details else None,
                evaluated_at=gate.evaluated_at,
            )
        )
    return rows


def map_certifications(scorecard: Scorecard, scorecard_row: ScorecardRow) -> list[CertificationRow]:
    """Create CertificationRow rows from scorecard certification results."""
    if scorecard.certification is None:
        return []

    rows: list[CertificationRow] = []
    for level_result in [
        scorecard.certification.foundational,
        scorecard.certification.trusted,
        scorecard.certification.certified,
    ]:
        failed = [c.check_id.value for c in level_result.checks if not c.passed]
        rows.append(
            CertificationRow(
                scorecard=scorecard_row,
                level=level_result.level.value,
                passed=level_result.passed,
                checks_total=level_result.checks_total,
                checks_passed=level_result.checks_passed,
                checks_failed=level_result.checks_total - level_result.checks_passed,
                failed_checks=failed if failed else None,
                details_json=json.loads(level_result.model_dump_json()),
            )
        )
    return rows


def store_snapshot(report_dir: Path, database_url: str | None, run_id: str) -> None:
    """Atomically store or complete report, scorecard, scans, and metrics for one run."""
    from sqlalchemy import delete

    scorecard = None
    scorecard_path = report_dir / "scorecard.json"
    if scorecard_path.is_file():
        scorecard = Scorecard.model_validate_json(scorecard_path.read_text())
    report_path = report_dir / "report.json"
    report = AnalysisResult.model_validate_json(report_path.read_text()) if report_path.is_file() else None
    if scorecard is None and report is None:
        raise ValueError("No valid report.json or scorecard.json is available")
    engine = get_engine(database_url)
    try:
        init_db(engine)
        with make_session(engine).begin() as session:
            if report:
                existing = session.scalar(select(EvaluationRun).where(EvaluationRun.pipeline_run_id == run_id))
                if existing is None:
                    row = map_result_to_run(report, run_id)
                    session.add(row)
                    session.flush()
                    session.add_all(map_trials(report, row))
            session.execute(delete(SecurityScan).where(SecurityScan.pipeline_run_id == run_id))
            if report:
                session.add_all(map_security_scans(report, run_id))
            if scorecard:
                if not report or not report.security_scans:
                    for gate in scorecard.gates:
                        if gate.gate_type.value != "security":
                            continue
                        findings = [f.model_dump(mode="json") for f in gate.findings]
                        session.add(
                            SecurityScan(
                                pipeline_run_id=run_id,
                                submission_name=scorecard.submission_name,
                                scanner=gate.get_policy_key(),
                                scan_mode=gate.mode.value,
                                passed=gate.passed,
                                findings_count=len(findings),
                                critical_count=sum(f["severity"] == "critical" for f in findings),
                                high_count=sum(f["severity"] == "high" for f in findings),
                                findings_json=findings,
                            )
                        )
                old = session.scalar(select(ScorecardRow).where(ScorecardRow.pipeline_run_id == run_id))
                if old:
                    session.delete(old)
                    session.flush()
                scorecard.pipeline_run_id = run_id
                row = map_scorecard_to_row(scorecard)
                session.add(row)
                session.flush()
                session.add_all(map_gate_results(scorecard, row))
                session.add_all(map_certifications(scorecard, row))
            metrics = MetricsContext.load_checkpoint(report_dir)
            if metrics and metrics.total_tokens:
                session.execute(
                    delete(ObservabilityMetricsRow).where(ObservabilityMetricsRow.pipeline_run_id == run_id)
                )
                session.add(ObservabilityMetricsRow(pipeline_run_id=run_id, **metrics.to_observability_dict()))
    finally:
        engine.dispose()
