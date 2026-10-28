"""Shared skill catalog fixtures for configuration and workflow contracts."""

import pytest


@pytest.fixture
def catalog(tmp_path):
    root = tmp_path / "catalog"
    skill = root / "pack/skills/demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Use when checking examples.\nlicense: Apache-2.0\n---\n# Demo\n"
    )
    submission = root / "submissions/pack/demo"
    submission.mkdir(parents=True)
    (submission / "metadata.yaml").write_text("name: demo\naeh_mode: pairwise\nskip_quality_review: false\n")
    return {"skill": skill, "source": root, "submission": submission, "output": tmp_path / "run", "run_id": "test-run"}


@pytest.fixture
def pipeline_evidence(catalog, evidence_database_url):
    """A complete pairwise tool report plus Tier 1 findings and review dimensions."""
    import json
    from datetime import UTC, datetime

    import yaml

    from abevalflow.config.runtime import resolve_config
    from abevalflow.gates.base import Finding, GateMode, GateResult, GateType, Severity
    from abevalflow.results.aeh import aggregate_pairwise_run
    from abevalflow.results.run import StepResult, write_json
    from abevalflow.scorecard import Scorecard
    from abevalflow.workflow.service import create_result

    config = resolve_config(
        {
            **catalog,
            "tiers": "1,2",
            "revision": "a" * 40,
            "built_image_ref": "quay.io/example/plugin@sha256:" + "b" * 64,
        },
        {},
    )
    reports = config.output / "reports"
    reports.mkdir(parents=True)
    for name, score in (("control", 0.3), ("treatment", 0.9)):
        directory = reports / name
        directory.mkdir()
        summary = {
            "run_id": name,
            "judges": {"accuracy": {"mean": score, "pass_rate": 1.0, "errored_cases": 0}},
            "per_case": {
                "case-1": {
                    "accuracy": {"value": score, "judge_type": "llm"},
                    "used_tool": {"value": True, "judge_type": "check"},
                },
                "case-2": {"accuracy": {"value": None, "judge_type": "llm", "error": "judge timeout"}},
            },
            "run_metrics": {"custom_metric": 7},
        }
        if name == "treatment":
            summary["pairwise"] = {
                "wins_a": 1,
                "wins_b": 0,
                "ties": 0,
                "errors": 1,
                "per_case": [
                    {"case_id": "case-1", "winner": "A", "reason": "Grounded answer"},
                    {"case_id": "case-2", "winner": "error", "error": "judge timeout"},
                ],
                "stability": {"agreement_rate": 1.0},
            }
        (directory / "summary.yaml").write_text(yaml.safe_dump(summary))
        (directory / "run_result.json").write_text(json.dumps({"mean_reward": score, "duration_s": 9}))
    eval_config = config.submission / "eval-treatment.yaml"
    eval_config.write_text("judges:\n- name: accuracy\n  score_range: [0, 1]\n")
    report = aggregate_pairwise_run(
        reports / "treatment",
        reports / "control",
        submission_name=config.name,
        treatment_config_path=eval_config,
        control_config_path=eval_config,
    )
    report["provenance"].update(commit_sha=config.revision, pipeline_run_id=config.run_id)
    write_json(reports / "report.json", report)
    findings = [
        Finding(severity=severity, message=f"{severity} finding", rule_id=f"rule-{severity}") for severity in Severity
    ]
    gates = [
        GateResult(
            gate_type=GateType.SECURITY,
            gate_name="security",
            policy_key="cisco",
            score=0,
            passed=False,
            mode=GateMode.BLOCK,
            findings=findings,
        ),
        GateResult(
            gate_type=GateType.QUALITY,
            gate_name="quality",
            policy_key="skillmd-quality",
            score=0.7,
            passed=True,
            details={"dimensions": {"clarity": {"score": 0.6, "finding": "Ambiguous"}}},
            findings=[Finding(severity=Severity.LOW, message="Unclear instructions")],
        ),
        GateResult(
            gate_type=GateType.ENGINE, gate_name="evaluation", policy_key="aeh", score=0.5, threshold=0.5, passed=True
        ),
    ]
    card = Scorecard(
        submission_name=config.name,
        pipeline_run_id=config.run_id,
        eval_engine="aeh",
        gates=gates,
        recommendation="fail",
        recommendation_reason="Security findings",
        provenance={"commit_sha": config.revision},
    )
    write_json(reports / "scorecard.json", card.model_dump(mode="json"))
    result = create_result(config)
    result.started_at = datetime(2026, 1, 1, tzinfo=UTC)
    result.completed_at = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    result.steps = [
        StepResult(
            name=name,
            tier=1
            if name in {"structure", "security", "quality"}
            else 2
            if name in {"submission", "submission-review", "evaluate"}
            else None,
            verdict="fail" if name == "security" else "pass",
        )
        for name in result.requested_steps
    ]
    result.steps[0].details = {"source_digest": "sha256:source"}
    return config, result, evidence_database_url


@pytest.fixture
def evidence_database_url(tmp_path):
    """Use SQLite normally; optionally isolate each integration case in a real PostgreSQL schema."""
    import os
    import uuid

    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    configured = os.environ.get("ABEVAL_TEST_DATABASE_URL")
    if not configured:
        yield f"sqlite:///{tmp_path / 'evidence.db'}"
        return
    schema = "evidence_test_" + uuid.uuid4().hex
    engine = create_engine(configured)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        yield (
            make_url(configured)
            .update_query_dict({"options": f"-csearch_path={schema}"})
            .render_as_string(hide_password=False)
        )
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()
