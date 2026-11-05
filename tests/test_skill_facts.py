"""Compass values agree with persisted scorecards, including pairwise runs."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from sqlalchemy import create_engine, text

from abevalflow.certification import compute_certification
from abevalflow.persistence.service import publish
from abevalflow.report import AnalysisResult
from abevalflow.results.run import write_json
from abevalflow.schemas import PushFactsConfig
from abevalflow.scorecard import Scorecard
from abevalflow.scorecard_values import score_100
from abevalflow.skill_facts import build_skill_facts


def inputs(config):
    reports = config.output / "reports"
    card = Scorecard.model_validate_json((reports / "scorecard.json").read_text())
    card.policy.push_facts = PushFactsConfig(
        endpoint="https://compass.example/api/soundcheck/facts/",
        entity_ref="airesource:ai5-marketplace/demo",
        fact_ref_prefix="ai5-marketplace:default/",
        bearer_token="test-token",
    )
    report = AnalysisResult.model_validate_json((reports / "report.json").read_text())
    return card, report


def test_publication_matches_database_scores_and_not_pairwise_gate(pipeline_evidence):
    config, result, url = pipeline_evidence
    card, _ = inputs(config)
    card.gates[0].score = 0.7355
    card.gates[1].score = 0.75655
    card.certification = compute_certification(gates=card.gates)
    write_json(config.output / "reports/scorecard.json", card.model_dump(mode="json"))
    config.persist = ("compass", "postgres")
    config = type(config).model_validate(config.model_dump() | {"database_url": url})
    response = MagicMock()
    response.__enter__.return_value.status = 200
    with patch("abevalflow.compass_facts.urllib.request.urlopen", return_value=response) as request:
        outcomes = publish(config, result)
    assert all(s.status == "completed" for s in outcomes)
    facts = [json.loads(call.args[0].data)["facts"][0] for call in request.call_args_list]
    assert all(json.loads(call.args[0].data)["cache"] is True for call in request.call_args_list)
    by_id = {f["factRef"].split("/")[-1]: f for f in facts}
    with create_engine(url).connect() as conn:
        row = conn.execute(text("SELECT * FROM latest_skill_scorecards")).mappings().one()
    expected = {
        "basic-security-validation": row["security_scan_score_100"],
        "advanced-security-validation": row["security_scan_score_100"],
        "content-quality-review": row["quality_scan_score_100"],
        "instruction-quality": row["quality_scan_score_100"],
        "enterprise-behavioral-evaluation": row["behavior_score_100"],
    }
    for check_id, value in expected.items():
        data = by_id[check_id]["data"]
        assert data["value"] == float(value)
        assert data["available"] is True
        assert data["run_id"] == result.run_id
        assert data["source_revision"] == result.source_revision
    assert by_id["enterprise-behavioral-evaluation"]["data"]["value"] == 90
    assert row["pairwise_win_rate_pct"] == 50
    structure = by_id["valid-skill-structure"]["data"]
    assert structure["value"] is True
    assert sum(f["factRef"].endswith("/valid-skill-structure") for f in facts) == 1
    saved = json.loads((config.output / "reports/scorecard.json").read_text())
    assert len(saved["skill_fact_push_results"]) == 6
    assert all(r["success"] for r in saved["skill_fact_push_results"])
    assert len(saved["certification_fact_push_results"]) == 4


def test_missing_evidence_clears_all_six_values_instead_of_reusing_old_scores(pipeline_evidence):
    config, result, _ = pipeline_evidence
    card, _ = inputs(config)
    result.steps = []
    facts = build_skill_facts(card, result, None)
    assert len(facts) == 6
    assert facts[0]["data"]["value"] is False
    assert all(f["data"]["value"] is None for f in facts[1:])
    assert all(f["data"]["available"] is False for f in facts)


def test_scan_failure_is_a_score_but_unavailable_scan_is_null(pipeline_evidence):
    config, result, _ = pipeline_evidence
    card, report = inputs(config)
    facts = build_skill_facts(card, result, report)
    assert facts[1]["data"]["value"] == 0
    assert facts[1]["data"]["available"] is True
    card.gates[0].details["scoring_unavailable"] = True
    facts = build_skill_facts(card, result, report)
    assert facts[1]["data"]["value"] is None
    assert facts[3]["data"]["value"] is None


def test_other_skill_report_is_rejected(pipeline_evidence):
    config, result, _ = pipeline_evidence
    card, report = inputs(config)
    report.submission_name = "another-skill"
    with pytest.raises(ValueError, match="different skill"):
        build_skill_facts(card, result, report)


def test_compass_rule_refs_and_paths_resolve_against_published_facts(pipeline_evidence):
    config, result, _ = pipeline_evidence
    card, report = inputs(config)
    facts = {fact["factRef"]: fact["data"] for fact in build_skill_facts(card, result, report)}
    path = Path(__file__).parents[1] / "Docs/examples/compass-skill-checks.yaml"
    checks = yaml.safe_load(path.read_text())["checks"]
    assert {check["id"] for check in checks} == {ref.split("/")[-1] for ref in facts}
    for check in checks:
        available, value = check["rule"]["all"]
        assert available["path"] == "$.available"
        assert available["operator"] == "equal" and available["value"] is True
        assert available["factRef"] == value["factRef"]
        assert value["path"] == "$.value"
        data = facts[value["factRef"]]
        if check["id"] == "valid-skill-structure":
            assert type(data["value"]) is bool
            assert value["operator"] == "equal" and value["value"] is True
        else:
            assert type(data["value"]) is float
            assert value["operator"] == "greaterThanInclusive"
            assert 0 < value["value"] <= 100


@pytest.mark.parametrize("value, expected", [(0, 0), (1, 100), (0.7345, 73.5), (None, None)])
def test_numeric_rounding_matches_database_convention(value, expected):
    assert score_100(value) == expected


def test_skill_fact_failure_fails_publication(pipeline_evidence):
    config, result, _ = pipeline_evidence
    card, _ = inputs(config)
    write_json(config.output / "reports/scorecard.json", card.model_dump(mode="json"))
    config.persist = ("compass",)

    def respond(request, **kwargs):
        response = MagicMock()
        fact = json.loads(request.data)["facts"][0]
        response.__enter__.return_value.status = 503 if fact["factRef"].endswith("/instruction-quality") else 200
        return response

    with patch("abevalflow.compass_facts.urllib.request.urlopen", side_effect=respond):
        outcomes = publish(config, result)
    assert outcomes[0].status == "error"
    saved = json.loads((config.output / "reports/scorecard.json").read_text())
    assert len(saved["skill_fact_push_results"]) == 6
    assert next(r for r in saved["skill_fact_push_results"] if r["level"] == "instruction-quality")["success"] is False
