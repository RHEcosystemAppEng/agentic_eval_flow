"""Persist an A/B evaluation report to the results database.

Usage::

    python scripts/store_results.py \\
        --report-dir /path/to/report-dir \\
        --database-url postgresql+psycopg://user:pass@host:5432/abevalflow

The script reads ``{report-dir}/report.json``, validates it against the
``AnalysisResult`` Pydantic model, and inserts one ``EvaluationRun`` row
plus one ``Trial`` row per trial into the database (plus a nested
``ScorecardRow``/``GateResultRow``/``CertificationRow`` set when
``scorecard.json`` is also present).

If ``report.json`` is absent but ``scorecard.json`` exists (a leg that only
ran cheap, non-LLM checks this run -- no eval, no report.json), see
``store_scorecard_only`` below: it persists the same
``ScorecardRow``/``GateResultRow``/``CertificationRow`` set standalone,
since none of those three tables have a foreign-key dependency on
``EvaluationRun``.

Idempotency: if ``pipeline_run_id`` already exists, the insert is skipped.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from abevalflow.db.engine import get_engine, init_db, make_session
from abevalflow.db.models import (
    CertificationRow,
    EvaluationRun,
    GateResultRow,
    MCPCheckerRun,
    MCPCheckerTask,
    ObservabilityMetricsRow,
    ScorecardRow,
    SecurityScan,
    Trial,
)
from abevalflow.db.observer import discover_observers, notify_observers
from abevalflow.mcpchecker_report import MCPCheckerResult
from abevalflow.observability.context import MetricsContext
from abevalflow.report import AnalysisResult
from abevalflow.scorecard import Scorecard

logger = logging.getLogger(__name__)


def _compute_content_hash(data: bytes) -> str:
    """Deterministic fallback key when no pipeline_run_id is provided."""
    return f"content-{hashlib.sha256(data).hexdigest()[:16]}"


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


def map_mcpchecker_result(result: MCPCheckerResult, run_id: str) -> MCPCheckerRun:
    """Create MCPCheckerRun row from MCPCheckerResult."""
    return MCPCheckerRun(
        pipeline_run_id=run_id,
        submission_name=result.submission_name,
        eval_name=result.eval_name,
        overall_score=result.overall_score,
        passed_tasks=result.passed_tasks,
        failed_tasks=result.failed_tasks,
        error_tasks=result.error_tasks,
        skipped_tasks=result.skipped_tasks,
        total_tasks=result.total_tasks,
        total_duration_ms=result.total_duration_ms,
        raw_output_json=json.loads(result.model_dump_json()),
    )


def map_mcpchecker_tasks(result: MCPCheckerResult, run: MCPCheckerRun) -> list[MCPCheckerTask]:
    """Create MCPCheckerTask rows from task results."""
    tasks: list[MCPCheckerTask] = []
    for task in result.tasks:
        llm_judge_passed = sum(1 for r in task.llm_judge_results if r.passed)
        llm_judge_total = len(task.llm_judge_results)

        tasks.append(
            MCPCheckerTask(
                run=run,
                task_id=task.task_id,
                task_name=task.task_name,
                status=task.status,
                tool_calls=task.tool_calls,
                duration_ms=task.duration_ms,
                error_message=task.error_message,
                llm_judge_passed=llm_judge_passed,
                llm_judge_total=llm_judge_total,
                details_json=task.model_dump() if task.llm_judge_results or task.tool_call_records else None,
            )
        )
    return tasks


def store_mcpchecker(
    report_dir: Path,
    database_url: str | None = None,
    run_id: str | None = None,
) -> bool:
    """Load, validate, and persist an MCPChecker report. Returns True on success."""
    report_path = report_dir / "mcpchecker-report.json"
    if not report_path.exists():
        logger.error("MCPChecker report not found: %s", report_path)
        return False

    raw = report_path.read_bytes()
    try:
        result = MCPCheckerResult.model_validate_json(raw)
    except Exception:
        logger.exception("Failed to validate MCPChecker report JSON")
        return False

    effective_run_id = run_id or _compute_content_hash(raw)
    logger.info("MCPChecker Run ID: %s", effective_run_id)

    engine = get_engine(database_url)
    init_db(engine)
    session_factory = make_session(engine)

    with session_factory() as session:
        existing = session.execute(
            select(MCPCheckerRun).where(MCPCheckerRun.pipeline_run_id == effective_run_id)
        ).scalar_one_or_none()

        if existing is not None:
            logger.warning(
                "MCPChecker run %s already exists (id=%s) — skipping",
                effective_run_id,
                existing.id,
            )
            return True

        mcp_run = map_mcpchecker_result(result, effective_run_id)
        mcp_tasks = map_mcpchecker_tasks(result, mcp_run)

        session.add(mcp_run)
        session.add_all(mcp_tasks)
        try:
            session.commit()
        except IntegrityError as e:
            session.rollback()
            logger.warning(
                "Concurrent insert for MCPChecker run %s — treating as idempotent: %s",
                effective_run_id,
                str(e)[:100],
            )
            return True

        logger.info(
            "Stored MCPChecker: submission=%s run_id=%s tasks=%d score=%.2f recommendation=%s",
            result.submission_name,
            effective_run_id,
            len(mcp_tasks),
            result.overall_score,
            result.recommendation,
        )

    return True


def store_scorecard_only(
    report_dir: Path,
    database_url: str | None = None,
    run_id: str | None = None,
) -> bool:
    """Persist a scorecard produced WITHOUT a full AnalysisResult -- e.g. a leg
    that only ran the cheap, non-LLM checks (security-scan/skillmd-scan) this
    run, with no LLM evaluation to produce report.json.

    Inserts ScorecardRow/GateResultRow/CertificationRow independent of
    EvaluationRun -- these three tables have no foreign-key dependency on
    EvaluationRun at all (see map_scorecard_to_row/map_gate_results/
    map_certifications and abevalflow.db.models.ScorecardRow), so a
    security/quality-only scorecard (no eval gate, no trials) is a fully
    valid, standalone row. This is what lets the merged Tekton evaluate-skill
    Task leverage store/analyze-and-scorecard (and their Compass Facts /
    Postgres persistence) for EVERY skill's cheap checks, not just skills
    that also went through the LLM evaluation chain this run.

    Returns True on success, including the idempotent "already exists" case.
    """
    scorecard_path = report_dir / "scorecard.json"
    raw = scorecard_path.read_bytes()
    try:
        scorecard = Scorecard.model_validate_json(raw)
    except Exception:
        logger.exception("Failed to validate scorecard JSON")
        return False

    effective_run_id = run_id or _compute_content_hash(raw)
    logger.info("Scorecard-only Run ID: %s", effective_run_id)

    engine = get_engine(database_url)
    init_db(engine)
    session_factory = make_session(engine)

    with session_factory() as session:
        existing = session.execute(
            select(ScorecardRow).where(ScorecardRow.pipeline_run_id == effective_run_id)
        ).scalar_one_or_none()

        if existing is not None:
            logger.warning(
                "Scorecard for run %s already exists (id=%s) -- skipping",
                effective_run_id,
                existing.id,
            )
            return True

        sc_row = map_scorecard_to_row(scorecard)
        gate_rows = map_gate_results(scorecard, sc_row)
        cert_rows = map_certifications(scorecard, sc_row)

        session.add(sc_row)
        session.add_all(gate_rows)
        session.add_all(cert_rows)

        try:
            session.commit()
        except IntegrityError as e:
            session.rollback()
            logger.warning(
                "Concurrent insert for scorecard run %s -- treating as idempotent: %s",
                effective_run_id,
                str(e)[:100],
            )
            return True

        logger.info(
            "Stored scorecard-only: submission=%s run_id=%s gates=%d certifications=%d recommendation=%s",
            scorecard.submission_name,
            effective_run_id,
            len(gate_rows),
            len(cert_rows),
            scorecard.recommendation.value,
        )

    return True


def store(
    report_dir: Path,
    database_url: str | None = None,
    run_id: str | None = None,
) -> bool:
    """Load, validate, and persist a report. Returns True on success.

    Supports A/B reports (report.json), MCPChecker reports
    (mcpchecker-report.json), and scorecard-only runs (scorecard.json with
    no report.json -- see store_scorecard_only). Checks in that order and
    stores whichever is found.
    """
    report_path = report_dir / "report.json"
    mcpchecker_report_path = report_dir / "mcpchecker-report.json"
    scorecard_path = report_dir / "scorecard.json"

    if mcpchecker_report_path.exists():
        logger.info("Found MCPChecker report, storing to mcpchecker_results table")
        return store_mcpchecker(report_dir, database_url, run_id)

    if not report_path.exists():
        if not scorecard_path.exists():
            logger.error("Neither report.json nor scorecard.json found in %s", report_dir)
            return False
        logger.info(
            "No report.json (no LLM evaluation ran this leg) -- storing scorecard-only "
            "run from %s",
            scorecard_path,
        )
        return store_scorecard_only(report_dir, database_url, run_id)

    raw = report_path.read_bytes()
    try:
        result = AnalysisResult.model_validate_json(raw)
    except Exception:
        logger.exception("Failed to validate report JSON")
        return False

    effective_run_id = run_id or _compute_content_hash(raw)
    logger.info("Run ID: %s", effective_run_id)

    engine = get_engine(database_url)
    init_db(engine)
    session_factory = make_session(engine)

    with session_factory() as session:
        existing = session.execute(
            select(EvaluationRun).where(EvaluationRun.pipeline_run_id == effective_run_id)
        ).scalar_one_or_none()

        if existing is not None:
            # Note: early return skips scorecard/metrics persistence for
            # pre-existing runs. Backfill via scripts/backfill_scorecards.py.
            logger.warning(
                "Run %s already exists (id=%s) — skipping",
                effective_run_id,
                existing.id,
            )
            return True

        ev_run = map_result_to_run(result, effective_run_id)
        trials = map_trials(result, ev_run)
        security_scans = map_security_scans(result, effective_run_id)

        # Check if security scans already exist (may have been inserted by
        # security-scan task's immediate persistence step)
        existing_scan = session.execute(
            select(SecurityScan).where(SecurityScan.pipeline_run_id == effective_run_id)
        ).scalar_one_or_none()

        session.add(ev_run)
        session.add_all(trials)
        if existing_scan is None:
            session.add_all(security_scans)
        else:
            logger.info(
                "Security scans already exist for run %s, skipping",
                effective_run_id,
            )
        scorecard_path = report_dir / "scorecard.json"
        if scorecard_path.exists():
            try:
                with session.begin_nested():
                    scorecard = Scorecard.model_validate_json(scorecard_path.read_bytes())
                    sc_row = map_scorecard_to_row(scorecard)
                    gate_rows = map_gate_results(scorecard, sc_row)
                    cert_rows = map_certifications(scorecard, sc_row)
                    session.add(sc_row)
                    session.add_all(gate_rows)
                    session.add_all(cert_rows)
                logger.info(
                    "Scorecard queued: gates=%d certifications=%d",
                    len(gate_rows),
                    len(cert_rows),
                )
            except Exception:
                logger.warning("Failed to persist scorecard — continuing without", exc_info=True)

        metrics_ctx = MetricsContext.load_checkpoint(report_dir)
        if metrics_ctx and metrics_ctx.total_tokens > 0:
            try:
                with session.begin_nested():
                    session.add(
                        ObservabilityMetricsRow(
                            pipeline_run_id=effective_run_id,
                            **metrics_ctx.to_observability_dict(),
                        )
                    )
                logger.info("Observability metrics queued: tokens=%s", metrics_ctx.total_tokens)
            except Exception:
                logger.warning("Failed to persist observability metrics — continuing without", exc_info=True)

        try:
            session.commit()
        except IntegrityError as e:
            session.rollback()
            logger.warning(
                "Concurrent insert for run %s — treating as idempotent: %s",
                effective_run_id,
                str(e)[:100],
            )
            return True

        logger.info(
            "Stored: submission=%s run_id=%s trials=%d security_scans=%d recommendation=%s",
            result.submission_name,
            effective_run_id,
            len(trials),
            len(security_scans),
            result.summary.recommendation.value,
        )

        observers = discover_observers()
        if observers:
            notify_observers(
                observers,
                result,
                ev_run.id,
                report_dir=report_dir,
                pipeline_run_id=effective_run_id,
            )

    return True


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Persist A/B evaluation results to the database",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        required=True,
        help="Directory containing report.json",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="SQLAlchemy database URL (default: DATABASE_URL env var)",
    )
    parser.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Pipeline run ID for idempotency (default: content hash of report)",
    )
    args = parser.parse_args()

    ok = store(args.report_dir, args.database_url, args.run_id)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
