"""Compass and signed evidence use the same measured values, independently of publication."""

import json
import shutil

import pytest

from abevalflow.report import AnalysisResult
from abevalflow.results.konflux import component_evidence, emit_results
from abevalflow.results.run import write_json
from abevalflow.schemas import PushFactsConfig
from abevalflow.scorecard import Scorecard
from abevalflow.skill_facts import SCORE_COLUMNS, SCORE_CONTRACT, build_skill_facts, build_skill_values


def test_compass_and_attestation_share_values_without_database_or_compass_dependency(pipeline_evidence, tmp_path):
    config, run, _ = pipeline_evidence
    card_file = config.output / "reports/scorecard.json"
    card = Scorecard.model_validate_json(card_file.read_text())
    card.policy.push_facts = PushFactsConfig(
        endpoint="https://compass.example/api/soundcheck/facts/",
        entity_ref="airesource:ai5-marketplace/demo",
        fact_ref_prefix="ai5-marketplace:default/",
    )
    card.gates[0].score = 0.9
    card.gates[1].score = 0.86
    report = AnalysisResult.model_validate_json((config.output / "reports/report.json").read_text())
    facts = build_skill_facts(card, run, report)
    card.policy.push_facts = None
    write_json(card_file, card.model_dump(mode="json"))

    batch = tmp_path / "batch"
    run_dir = batch / "runs/0000"
    run_dir.mkdir(parents=True)
    shutil.copytree(config.output / "reports", run_dir / "reports")
    write_json(run_dir / "run.json", run.model_dump(mode="json"))
    skill_id = "pack/demo"
    write_json(
        batch / "batch.json",
        dict(
            schema_version="1.0",
            batch_id="batch",
            source_revision=run.source_revision,
            tiers=[1, 2],
            persist=[],
            started_at=run.started_at.isoformat(),
            completed_at=run.completed_at.isoformat(),
            items=[dict(skill_id=skill_id, run_dir="runs/0000", run_id=run.run_id, status="completed", exit_code=0)],
        ),
    )
    manifest = tmp_path / "manifest.json"
    write_json(
        manifest,
        dict(
            schema_version="1.0",
            plugin_id="pack",
            source_revision=run.source_revision,
            payload_digest="sha256:" + "c" * 64,
            skills=[skill_id],
        ),
    )
    image, digest = config.built_image_ref.split("@")
    evidence = component_evidence(batch, manifest, image, digest)
    assert evidence["schema_version"] == "2.0"
    assert evidence["score_contract"] == SCORE_CONTRACT
    assert evidence["score_columns"] == list(SCORE_COLUMNS)
    observations = evidence["evaluations"][0]["skill_facts"]
    for fact in facts:
        check = fact["factRef"].rsplit("/", 1)[1]
        for field in ("value", "available", "source", "value_type", "run_id", "source_revision", "evaluated_at"):
            assert observations[check][field] == fact["data"][field]
    # Treatment mean reward (90) is retained instead of the pairwise gate score (50).
    assert evidence["skill_scores"][skill_id] == [True, 90, 86, 90]
    results = tmp_path / "results"
    attachment = tmp_path / "attachment.json"
    write_json(attachment, {"uri": image, "digest": "sha256:" + "d" * 64})
    emit_results(
        batch,
        [skill_id],
        run.source_revision,
        {1, 2},
        set(),
        results,
        image,
        digest,
        attachment,
        fail_on_non_success=False,
    )
    summary = json.loads((results / "PLUGIN_EVALUATION").read_text())
    assert summary["skill_scores"] == evidence["skill_scores"]
    assert summary["score_columns"] == evidence["score_columns"]
    assert summary["evidence_digest"].startswith("sha256:")
    assert len((results / "PLUGIN_EVALUATION").read_bytes()) < 3000


@pytest.mark.parametrize("status", ["error", "skipped"])
def test_unavailable_scan_is_null_not_zero_or_a_previous_score(pipeline_evidence, status):
    config, run, _ = pipeline_evidence
    card = Scorecard.model_validate_json((config.output / "reports/scorecard.json").read_text())
    next(s for s in run.steps if s.name == "security").status = status
    observations = build_skill_values(card, run, None)
    for check in ("basic-security-validation", "advanced-security-validation"):
        assert observations[check]["value"] is None
        assert observations[check]["available"] is False
    assert observations["enterprise-behavioral-evaluation"]["value"] is None


@pytest.mark.parametrize("field,value", [("commit_sha", "other-revision"), ("pipeline_run_id", "other-run")])
def test_behavior_cannot_borrow_another_run_or_revision(pipeline_evidence, field, value):
    config, run, _ = pipeline_evidence
    card = Scorecard.model_validate_json((config.output / "reports/scorecard.json").read_text())
    report = AnalysisResult.model_validate_json((config.output / "reports/report.json").read_text())
    setattr(report.provenance, field, value)
    with pytest.raises(ValueError, match="different run or source revision"):
        build_skill_values(card, run, report)
