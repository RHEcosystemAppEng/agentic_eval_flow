"""Preserve tool-emitted certification, timing and token details in evaluation snapshots."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from abevalflow.db.models import CertificationResult, EvaluationMetrics
from abevalflow.observability.context import MetricsContext
from abevalflow.persistence.postgres import store_snapshot
from abevalflow.workflow.service import run
from tests.test_workflow import configure


def test_metrics_preserve_phase_details_and_zero_token_timing(pipeline_evidence):
    config, result, url = pipeline_evidence
    metrics = MetricsContext(run_id=config.run_id, submission_name=config.name)
    metrics.start_timing("prepare")
    metrics.stop_timing("prepare")
    metrics.checkpoint(config.output / "reports")
    store_snapshot(config, result, url)
    with Session(create_engine(url)) as session:
        row = session.scalar(select(EvaluationMetrics))
        assert row.prepare_duration_ms is not None
        assert row.phase_metrics["timings"]["prepare"]["duration_ms"] is not None
    metrics.record_tokens("submission-review", 100, 40, "example-model")
    metrics.checkpoint(config.output / "reports")
    store_snapshot(config, result, url)
    with Session(create_engine(url)) as session:
        row = session.scalar(select(EvaluationMetrics))
        assert row.total_tokens == 140
        assert row.phase_metrics["token_usage"]["submission-review"]["prompt_tokens"] == 100
        assert len(session.scalars(select(EvaluationMetrics)).all()) == 1


def test_certifications_come_from_pipeline_scorecard(catalog, tmp_path):
    config = configure(catalog, tmp_path, revision="a" * 40)
    result = run(config)
    url = f"sqlite:///{tmp_path / 'certification.db'}"
    store_snapshot(config, result, url)
    with Session(create_engine(url)) as session:
        certifications = session.scalars(select(CertificationResult)).all()
        assert len(certifications) == 3
        assert {c.level for c in certifications} == {"foundational", "trusted", "certified"}
        assert all(isinstance(c.checks, list) for c in certifications)
