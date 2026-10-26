"""Tests for scorecard persistence in store_results.py."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from abevalflow.db.models import (
    Base,
    EvaluationRun,
    GateResultRow,
    ObservabilityMetricsRow,
    ScorecardRow,
)
from abevalflow.gates.base import GateMode, GateResult, GateType
from abevalflow.persistence.postgres import (
    map_certifications,
    map_gate_results,
    map_scorecard_to_row,
    store,
)
from abevalflow.report import (
    AnalysisResult,
    AnalysisSummary,
    Provenance,
    Recommendation,
    TrialResult,
    VariantSummary,
)
from abevalflow.schemas import CombinationMode, GatePolicy
from abevalflow.scorecard import Recommendation as ScorecardRecommendation
from abevalflow.scorecard import Scorecard


def _sample_result(name: str = "my-submission") -> AnalysisResult:
    return AnalysisResult(
        submission_name=name,
        provenance=Provenance(
            commit_sha="abc123",
            pipeline_run_id="tekton-run-001",
            treatment_image_ref="registry/img@sha256:aaa",
            control_image_ref="registry/img@sha256:bbb",
            harbor_fork_revision="main",
        ),
        summary=AnalysisSummary(
            treatment=VariantSummary(
                n_trials=20,
                n_passed=16,
                n_failed=3,
                n_errors=1,
                pass_rate=0.8,
                mean_reward=0.72,
                median_reward=0.75,
                std_reward=0.15,
            ),
            control=VariantSummary(
                n_trials=20,
                n_passed=10,
                n_failed=8,
                n_errors=2,
                pass_rate=0.5,
                mean_reward=0.45,
                median_reward=0.42,
                std_reward=0.20,
            ),
            uplift=0.3,
            mean_reward_gap=0.27,
            ttest_p_value=0.02,
            fisher_p_value=0.04,
            recommendation=Recommendation.PASS,
        ),
        trials={
            "treatment": [TrialResult(trial_name=f"t-{i:03d}", reward=0.7 + 0.02 * i) for i in range(5)],
            "control": [TrialResult(trial_name=f"c-{i:03d}", reward=0.3 + 0.03 * i) for i in range(5)],
        },
    )


def _sample_scorecard(run_id: str = "tekton-run-001") -> Scorecard:
    return Scorecard(
        submission_name="my-submission",
        pipeline_run_id=run_id,
        eval_engine="harbor",
        gates=[
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="harbor",
                passed=True,
                score=0.85,
                mode=GateMode.BLOCK,
                threshold=0.0,
                message="Treatment outperforms control",
            ),
            GateResult(
                gate_type=GateType.SECURITY,
                gate_name="security",
                policy_key="cisco",
                passed=True,
                score=1.0,
                mode=GateMode.WARN,
            ),
        ],
        policy=GatePolicy(combination=CombinationMode.ALL_PASS),
        recommendation=ScorecardRecommendation.PASS,
        recommendation_reason="All gates passed",
    )


def _write_report(tmp_path: Path, result: AnalysisResult) -> Path:
    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text(result.model_dump_json(indent=2))
    return report_dir


def _write_scorecard(report_dir: Path, scorecard: Scorecard) -> None:
    (report_dir / "scorecard.json").write_text(scorecard.model_dump_json(indent=2))


@pytest.fixture()
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture()
def session_factory(db_url):
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


class TestScorecardMapping:
    def test_map_scorecard_to_row(self) -> None:
        sc = _sample_scorecard()
        row = map_scorecard_to_row(sc)
        assert row.submission_name == "my-submission"
        assert row.pipeline_run_id == "tekton-run-001"
        assert row.recommendation == "pass"
        assert row.combination_mode == "all_pass"
        assert row.gates_passed == 2
        assert row.gates_failed == 0
        assert row.highest_certification == "none"
        assert row.scorecard_json["submission_name"] == "my-submission"

    def test_map_gate_results(self) -> None:
        sc = _sample_scorecard()
        sc_row = map_scorecard_to_row(sc)
        gates = map_gate_results(sc, sc_row)
        assert len(gates) == 2
        assert gates[0].gate_name == "evaluation"
        assert gates[0].policy_key == "harbor"
        assert gates[0].passed is True
        assert gates[0].score == 0.85
        assert gates[1].gate_name == "security"
        assert gates[1].policy_key == "cisco"

    def test_map_certifications_none(self) -> None:
        sc = _sample_scorecard()
        sc_row = map_scorecard_to_row(sc)
        certs = map_certifications(sc, sc_row)
        assert certs == []


class TestStoreWithScorecard:
    def test_store_with_scorecard_json(self, tmp_path: Path, db_url: str, session_factory) -> None:
        result = _sample_result()
        report_dir = _write_report(tmp_path, result)
        scorecard = _sample_scorecard()
        _write_scorecard(report_dir, scorecard)

        ok = store(report_dir, db_url, "tekton-run-001")
        assert ok is True

        with session_factory() as session:
            assert session.execute(select(EvaluationRun)).scalar_one() is not None
            sc = session.execute(select(ScorecardRow)).scalar_one()
            assert sc.submission_name == "my-submission"
            assert sc.recommendation == "pass"

            gates = session.execute(select(GateResultRow)).scalars().all()
            assert len(gates) == 2

    def test_store_without_scorecard_json(self, tmp_path: Path, db_url: str, session_factory) -> None:
        result = _sample_result()
        report_dir = _write_report(tmp_path, result)

        ok = store(report_dir, db_url, "tekton-run-002")
        assert ok is True

        with session_factory() as session:
            assert session.execute(select(EvaluationRun)).scalar_one() is not None
            assert session.execute(select(ScorecardRow)).scalar_one_or_none() is None

    def test_store_with_malformed_scorecard(self, tmp_path: Path, db_url: str, session_factory) -> None:
        result = _sample_result()
        report_dir = _write_report(tmp_path, result)
        (report_dir / "scorecard.json").write_text("{ invalid json !!!")

        ok = store(report_dir, db_url, "tekton-run-003")
        assert ok is True

        with session_factory() as session:
            assert session.execute(select(EvaluationRun)).scalar_one() is not None
            assert session.execute(select(ScorecardRow)).scalar_one_or_none() is None

    def test_store_idempotent_with_scorecard(self, tmp_path: Path, db_url: str, session_factory) -> None:
        result = _sample_result()
        report_dir = _write_report(tmp_path, result)
        scorecard = _sample_scorecard()
        _write_scorecard(report_dir, scorecard)

        store(report_dir, db_url, "tekton-run-004")
        store(report_dir, db_url, "tekton-run-004")

        with session_factory() as session:
            scorecards = session.execute(select(ScorecardRow)).scalars().all()
            assert len(scorecards) == 1

    def test_scorecard_unique_constraint(self, db_url: str, session_factory) -> None:
        """Verify scorecard pipeline_run_id uniqueness at the DB level."""
        from sqlalchemy.exc import IntegrityError

        with session_factory() as session:
            session.add(
                ScorecardRow(
                    pipeline_run_id="run-dup-test",
                    submission_name="test",
                    eval_engine="harbor",
                    recommendation="pass",
                    recommendation_reason="All gates passed",
                    combination_mode="all_pass",
                    gates_passed=1,
                    gates_failed=0,
                    blocking_gates_passed=1,
                    blocking_gates_failed=0,
                    highest_certification="none",
                    scorecard_json={},
                )
            )
            session.commit()

            session.add(
                ScorecardRow(
                    pipeline_run_id="run-dup-test",
                    submission_name="test-2",
                    eval_engine="harbor",
                    recommendation="fail",
                    recommendation_reason="Failed",
                    combination_mode="all_pass",
                    gates_passed=0,
                    gates_failed=1,
                    blocking_gates_passed=0,
                    blocking_gates_failed=1,
                    highest_certification="none",
                    scorecard_json={},
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()

    def test_store_with_metrics_checkpoint(self, tmp_path: Path, db_url: str, session_factory) -> None:
        from abevalflow.observability.context import MetricsContext, TimingRecord

        result = _sample_result()
        report_dir = _write_report(tmp_path, result)
        scorecard = _sample_scorecard()
        _write_scorecard(report_dir, scorecard)

        ctx = MetricsContext(
            run_id="tekton-run-metrics",
            submission_name="my-submission",
            model_name="claude-sonnet",
        )
        ctx.record_tokens("quality_review", 500, 200, "claude-sonnet")
        ctx.timings["pipeline"] = TimingRecord(name="pipeline", start_time=0, end_time=10, duration_ms=10000)
        ctx.checkpoint(report_dir)

        ok = store(report_dir, db_url, "tekton-run-metrics")
        assert ok is True

        with session_factory() as session:
            metrics = session.execute(select(ObservabilityMetricsRow)).scalar_one()
            assert metrics.submission_name == "my-submission"
            assert metrics.model_name == "claude-sonnet"
            assert metrics.total_prompt_tokens == 500
            assert metrics.total_completion_tokens == 200
            assert metrics.total_tokens == 700
            assert metrics.pipeline_duration_ms == 10000
            assert metrics.llm_calls_count == 1

    def test_store_without_metrics_checkpoint(self, tmp_path: Path, db_url: str, session_factory) -> None:
        result = _sample_result()
        report_dir = _write_report(tmp_path, result)

        ok = store(report_dir, db_url, "tekton-run-no-metrics")
        assert ok is True

        with session_factory() as session:
            assert session.execute(select(ObservabilityMetricsRow)).scalar_one_or_none() is None
