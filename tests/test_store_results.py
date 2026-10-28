"""Full pairwise projection, score lineage, severity counts, and transactional retries."""

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from abevalflow.db.models import (
    CaseResult,
    CheckResult,
    CheckScore,
    EvaluationVariant,
    EvidenceArtifact,
    Finding,
    JudgeResult,
    JudgeSummary,
    PairwiseCaseResult,
    PairwiseResult,
    SkillEvaluation,
)
from abevalflow.persistence.postgres import store_snapshot
from abevalflow.report import AnalysisResult
from abevalflow.results.run import StepResult


def test_pairwise_scores_and_both_arms_survive_report_validation(pipeline_evidence):
    config, result, url = pipeline_evidence
    report = AnalysisResult.model_validate_json((config.output / "reports/report.json").read_text())
    assert report.control["judges"]["accuracy"]["mean"] == 0.3
    assert report.treatment["judges"]["accuracy"]["mean"] == 0.9
    store_snapshot(config, result, url)
    with Session(create_engine(url)) as session:
        evaluation = session.scalar(select(SkillEvaluation))
        assert evaluation.commit_sha == "a" * 40
        assert evaluation.built_image_ref == config.built_image_ref
        assert evaluation.source_digest == "sha256:source"
        assert evaluation.skill.plugin.name == "pack"
        assert evaluation.skill.name == "demo"
        arms = {v.name: v for v in evaluation.variants}
        assert arms["control"].mean_reward == pytest.approx(0.3)
        assert arms["treatment"].mean_reward == pytest.approx(0.9)
        assert arms["control"].run_metrics["custom_metric"] == 7
        assert session.scalar(select(func.count()).select_from(CaseResult)) == 4
        assert session.scalar(select(func.count()).select_from(JudgeResult)) == 6
        assert session.scalar(select(func.count()).select_from(JudgeSummary)) == 2
        pairwise = evaluation.pairwise
        assert pairwise.win_rate == 0.5
        assert pairwise.errors == 1
        case = next(c for c in pairwise.cases if c.case_key == "case-1")
        assert case.outcome == "treatment"
        assert case.details["reason"] == "Grounded answer"
        assert session.get(CaseResult, case.control_case_id).variant.name == "control"
        assert session.get(CaseResult, case.treatment_case_id).variant.name == "treatment"
        judge = next(j for j in session.scalars(select(JudgeResult)) if j.error)
        assert judge.raw_value is None and judge.normalized_score is None
        assert judge.case.reward is None and judge.case.passed is None
        numeric = next(j for j in session.scalars(select(JudgeResult)) if j.raw_value == 0.9)
        assert numeric.score_range == [0.0, 1.0]
        assert numeric.normalized_score == pytest.approx(0.9)


def test_findings_and_review_dimensions_are_queryable_per_check(pipeline_evidence):
    config, result, url = pipeline_evidence
    store_snapshot(config, result, url)
    with Session(create_engine(url)) as session:
        counts = (
            session.execute(
                text("""
            SELECT c.name, f.* FROM check_results c JOIN check_finding_counts f ON f.check_id = c.id
        """)
            )
            .mappings()
            .all()
        )
        security = next(c for c in counts if c["name"] == "security")
        assert security["findings_count"] == 5
        assert all(security[f"{severity}_count"] == 1 for severity in ("critical", "high", "medium", "low", "info"))
        quality = next(c for c in counts if c["name"] == "quality")
        assert quality["low_count"] == 1
        assert session.scalar(select(CheckScore)).score == 0.6
        assert session.scalar(select(func.count()).select_from(Finding)) == 6


def test_retry_keeps_identity_and_does_not_duplicate_children(pipeline_evidence):
    config, result, url = pipeline_evidence
    store_snapshot(config, result, url)
    with Session(create_engine(url)) as session:
        identity = session.scalar(select(SkillEvaluation.id))
    result.publication = [
        StepResult(
            name="s3",
            details={
                "objects": [
                    {
                        "name": "reports/report.json",
                        "uri": "s3://bucket/pack/demo/run/reports/report.json",
                        "sha256": "a" * 64,
                    }
                ]
            },
        )
    ]
    store_snapshot(config, result, url)
    store_snapshot(config, result, url)
    with Session(create_engine(url)) as session:
        assert session.scalar(select(SkillEvaluation.id)) == identity
        for model, count in (
            (SkillEvaluation, 1),
            (PairwiseResult, 1),
            (PairwiseCaseResult, 2),
            (CaseResult, 4),
            (EvaluationVariant, 2),
            (Finding, 6),
            (EvidenceArtifact, 1),
        ):
            assert session.scalar(select(func.count()).select_from(model)) == count


def test_failed_projection_rolls_back_whole_snapshot(pipeline_evidence):
    config, result, url = pipeline_evidence
    store_snapshot(config, result, url)
    with patch("abevalflow.persistence.postgres._variants", side_effect=ValueError("bad report")):
        with pytest.raises(ValueError):
            store_snapshot(config, result, url)
    with Session(create_engine(url)) as session:
        assert session.scalar(select(func.count()).select_from(CaseResult)) == 4
        assert session.scalar(select(func.count()).select_from(CheckResult)) == 6


def test_partial_run_without_scorecard_or_aeh_report_still_has_provenance(pipeline_evidence):
    config, result, url = pipeline_evidence
    for name in ("report.json", "scorecard.json"):
        (config.output / "reports" / name).unlink()
    result.steps = [StepResult(name="prepare", status="error", verdict="unavailable", reason="Source unavailable")]
    store_snapshot(config, result, url)
    with Session(create_engine(url)) as session:
        evaluation = session.scalar(select(SkillEvaluation))
        assert evaluation.status == "error" and not evaluation.evidence_complete
        assert evaluation.commit_sha == config.revision
        assert evaluation.execution_details["prepare"]["reason"] == "Source unavailable"
        assert evaluation.recommendation is None
        assert all(t.status == "partial" for t in evaluation.tiers)


def test_unknown_revision_and_reused_run_identity_are_rejected(pipeline_evidence):
    config, result, url = pipeline_evidence
    with pytest.raises(ValueError, match="commit SHA"):
        store_snapshot(config, result.model_copy(update={"source_revision": "unknown"}), url)
    store_snapshot(config, result, url)
    other_config = config.model_copy(update={"revision": "c" * 40})
    other_result = result.model_copy(update={"source_revision": "c" * 40})
    with pytest.raises(ValueError, match="different skill or revision"):
        store_snapshot(other_config, other_result, url)


def test_explicit_report_scans_are_not_lost_or_double_counted(pipeline_evidence):
    import json

    config, result, url = pipeline_evidence
    path = config.output / "reports/report.json"
    report = json.loads(path.read_text())
    report["security_scans"] = [
        {
            "scanner": "another-scanner",
            "scan_mode": "warn",
            "passed": True,
            "findings": [
                {"scanner": "another-scanner", "severity": "low", "rule_id": "scan-rule", "message": "Detail"}
            ],
        },
    ]
    path.write_text(json.dumps(report))
    store_snapshot(config, result, url)
    store_snapshot(config, result, url)
    with Session(create_engine(url)) as session:
        assert session.scalar(select(func.count()).select_from(Finding)) == 7
        check = session.scalar(select(CheckResult).where(CheckResult.tool == "another-scanner"))
        assert check.findings[0].severity == "low"


def test_concurrent_publication_retries_share_one_evaluation(pipeline_evidence):
    from concurrent.futures import ThreadPoolExecutor

    config, result, url = pipeline_evidence
    if not url.startswith("postgresql"):
        pytest.skip("Run with ABEVAL_TEST_DATABASE_URL for PostgreSQL concurrency coverage")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(store_snapshot, config, result, url) for _ in range(2)]
        for future in futures:
            future.result()
    with Session(create_engine(url)) as session:
        assert session.scalar(select(func.count()).select_from(SkillEvaluation)) == 1
        assert session.scalar(select(func.count()).select_from(CaseResult)) == 4
