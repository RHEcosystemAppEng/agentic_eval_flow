"""PostgreSQL evaluation and trial row mapping."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from abevalflow.db.models import Base
from abevalflow.persistence.postgres import (
    map_result_to_run,
    map_trials,
)
from abevalflow.report import (
    AnalysisResult,
    AnalysisSummary,
    Provenance,
    Recommendation,
    TrialResult,
    VariantSummary,
)


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


def _write_report(tmp_path: Path, result: AnalysisResult) -> Path:
    report_dir = tmp_path / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text(result.model_dump_json(indent=2))
    return report_dir


@pytest.fixture()
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'test.db'}"


@pytest.fixture()
def session_factory(db_url):
    engine = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


# ---------------------------------------------------------------------------
# Mapping tests
# ---------------------------------------------------------------------------


class TestMapping:
    def test_map_result_to_run(self):
        result = _sample_result()
        run = map_result_to_run(result, "run-001")
        assert run.submission_name == "my-submission"
        assert run.pipeline_run_id == "run-001"
        assert run.recommendation == "pass"
        assert run.uplift == pytest.approx(0.3)
        assert run.treatment_pass_rate == pytest.approx(0.8)
        assert run.control_n_errors == 2
        assert run.commit_sha == "abc123"
        assert run.report_json["submission_name"] == "my-submission"

    def test_map_trials(self):
        result = _sample_result()
        run = map_result_to_run(result, "run-001")
        trials = map_trials(result, run)
        assert len(trials) == 10
        treatment = [t for t in trials if t.variant == "treatment"]
        control = [t for t in trials if t.variant == "control"]
        assert len(treatment) == 5
        assert len(control) == 5
        assert all(t.passed for t in treatment)
        assert treatment[0].trial_name == "t-000"

    def test_map_trial_with_null_reward(self):
        result = _sample_result()
        result.trials["treatment"].append(TrialResult(trial_name="error-trial", reward=None))
        run = map_result_to_run(result, "run-001")
        trials = map_trials(result, run)
        error_trial = [t for t in trials if t.trial_name == "error-trial"][0]
        assert error_trial.reward is None
        assert error_trial.passed is False


# ---------------------------------------------------------------------------
# Store function tests
# ---------------------------------------------------------------------------
