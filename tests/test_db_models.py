"""Tests for abevalflow.db models, engine, and observer protocol."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from abevalflow.db.engine import get_engine, init_db, make_session
from abevalflow.db.models import Base, EvaluationRun, Trial


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine) -> Session:
    factory = sessionmaker(bind=engine)
    with factory() as s:
        yield s


def _make_run(**overrides) -> EvaluationRun:
    defaults = {
        "submission_name": "test-submission",
        "pipeline_run_id": f"run-{uuid.uuid4().hex[:8]}",
        "recommendation": "pass",
        "uplift": 0.3,
        "treatment_n_trials": 20,
        "treatment_n_passed": 16,
        "treatment_n_failed": 3,
        "treatment_n_errors": 1,
        "treatment_pass_rate": 0.8,
        "control_n_trials": 20,
        "control_n_passed": 10,
        "control_n_failed": 8,
        "control_n_errors": 2,
        "control_pass_rate": 0.5,
        "report_json": {"submission_name": "test-submission"},
    }
    defaults.update(overrides)
    return EvaluationRun(**defaults)


# ---------------------------------------------------------------------------
# Model creation and relationships
# ---------------------------------------------------------------------------


class TestEvaluationRun:
    def test_create_run(self, session: Session):
        run = _make_run()
        session.add(run)
        session.commit()

        loaded = session.execute(
            select(EvaluationRun).where(EvaluationRun.pipeline_run_id == run.pipeline_run_id)
        ).scalar_one()
        assert loaded.submission_name == "test-submission"
        assert loaded.recommendation == "pass"
        assert loaded.uplift == pytest.approx(0.3)
        assert loaded.id is not None

    def test_created_at_default(self, session: Session):
        run = _make_run()
        session.add(run)
        session.commit()
        assert run.created_at is not None
        # SQLite strips tzinfo; PostgreSQL preserves it.
        # Just verify the timestamp is recent (within last 60s).
        from datetime import timedelta

        assert abs(datetime.now(UTC).replace(tzinfo=None) - run.created_at.replace(tzinfo=None)) < timedelta(seconds=60)

    def test_unique_pipeline_run_id(self, session: Session):
        run_id = "duplicate-run"
        session.add(_make_run(pipeline_run_id=run_id))
        session.commit()
        session.add(_make_run(pipeline_run_id=run_id))
        with pytest.raises(Exception):
            session.commit()

    def test_nullable_provenance(self, session: Session):
        run = _make_run(
            commit_sha=None,
            treatment_image_ref=None,
            control_image_ref=None,
            harbor_fork_revision=None,
            mean_reward_gap=None,
            ttest_p_value=None,
            fisher_p_value=None,
        )
        session.add(run)
        session.commit()
        assert run.commit_sha is None
        assert run.mean_reward_gap is None

    def test_report_json_round_trip(self, session: Session):
        report = {"submission_name": "foo", "nested": {"key": [1, 2, 3]}}
        run = _make_run(report_json=report)
        session.add(run)
        session.commit()

        loaded = session.get(EvaluationRun, run.id)
        assert loaded.report_json == report

    def test_repr(self):
        run = _make_run()
        r = repr(run)
        assert "test-submission" in r
        assert "pass" in r


class TestTrial:
    def test_create_trial(self, session: Session):
        run = _make_run()
        _trial = Trial(  # Created via run relationship, loaded separately below
            run=run,
            variant="treatment",
            trial_name="trial-001",
            reward=0.85,
            passed=True,
        )
        session.add(run)
        session.commit()

        loaded = session.execute(select(Trial).where(Trial.trial_name == "trial-001")).scalar_one()
        assert loaded.variant == "treatment"
        assert loaded.reward == pytest.approx(0.85)
        assert loaded.passed is True
        assert loaded.run_id == run.id

    def test_trial_with_null_reward(self, session: Session):
        run = _make_run()
        trial = Trial(
            run=run,
            variant="control",
            trial_name="error-trial",
            reward=None,
            passed=False,
        )
        session.add(run)
        session.commit()
        assert trial.reward is None
        assert trial.passed is False

    def test_cascade_delete(self, session: Session):
        run = _make_run()
        for i in range(5):
            Trial(
                run=run,
                variant="treatment",
                trial_name=f"trial-{i:03d}",
                reward=0.5 + 0.1 * i,
                passed=True,
            )
        session.add(run)
        session.commit()
        assert session.execute(select(Trial)).scalars().all()

        session.delete(run)
        session.commit()
        remaining = session.execute(select(Trial)).scalars().all()
        assert remaining == []

    def test_relationship_back_populates(self, session: Session):
        run = _make_run()
        _t1 = Trial(run=run, variant="treatment", trial_name="t1", reward=0.9, passed=True)
        _t2 = Trial(run=run, variant="control", trial_name="c1", reward=0.0, passed=False)
        session.add(run)
        session.commit()

        assert len(run.trials) == 2
        assert {t.trial_name for t in run.trials} == {"t1", "c1"}

    def test_repr(self):
        t = Trial(variant="treatment", trial_name="t-001", reward=0.75, passed=True)
        r = repr(t)
        assert "t-001" in r
        assert "treatment" in r


# ---------------------------------------------------------------------------
# Engine and init_db
# ---------------------------------------------------------------------------


class TestEngine:
    def test_get_engine_sqlite_default(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        eng = get_engine("sqlite://")
        assert eng.url.get_backend_name() == "sqlite"

    def test_get_engine_from_env(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "sqlite:///test.db")
        eng = get_engine()
        assert "test.db" in str(eng.url)

    def test_get_engine_explicit_url_overrides_env(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "sqlite:///env.db")
        eng = get_engine("sqlite:///explicit.db")
        assert "explicit.db" in str(eng.url)

    def test_init_db_idempotent(self):
        eng = create_engine("sqlite://")
        init_db(eng)
        init_db(eng)
        assert "evaluation_runs" in Base.metadata.tables

    def test_make_session(self):
        eng = create_engine("sqlite://")
        init_db(eng)
        factory = make_session(eng)
        with factory() as s:
            assert isinstance(s, Session)


# ---------------------------------------------------------------------------
# Observer protocol
# ---------------------------------------------------------------------------
