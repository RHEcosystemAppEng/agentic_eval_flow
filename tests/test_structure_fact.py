"""Standalone skill structure facts sent through the certification publisher."""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from abevalflow.certification import CheckId, compute_certification
from abevalflow.compass_facts import push_certification_facts
from abevalflow.schemas import PushFactsConfig
from abevalflow.skill_structure import validate_skill_structure


@pytest.fixture
def compass(monkeypatch):
    monkeypatch.setenv("COMPASS_TEST_TOKEN", "test-token")
    config = PushFactsConfig(
        endpoint="https://example.com/api/facts/",
        entity_ref="airesource:ai5-marketplace/test-skill",
        fact_ref_prefix="ai5-marketplace:default/",
        bearer_token="${COMPASS_TEST_TOKEN}",
    )
    with patch("abevalflow.compass_facts.urllib.request.urlopen") as urlopen:
        response = MagicMock()
        response.status = 200
        urlopen.return_value.__enter__.return_value = response
        yield config, urlopen


@pytest.mark.parametrize("validation_passed", [True, False])
def test_publishes_structure_result_independently_of_level(compass, tmp_path, validation_passed):
    config, urlopen = compass
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: Use when testing\n"
        + ("license: Apache-2.0\n" if validation_passed else "")
        + "---\n# Test\n"
    )
    certification = compute_certification(
        gates=[],
        validation_passed=not validation_passed,
        skill_structure_result=validate_skill_structure(skill_dir),
    )
    # Missing gate evidence fails the level even when structure itself passes.
    assert certification.foundational.passed is False

    results = push_certification_facts(certification, config)
    facts = [json.loads(call.args[0].data)["facts"][0] for call in urlopen.call_args_list]
    by_ref = {fact["factRef"]: fact for fact in facts}
    assert len(results) == len(facts) == 5
    assert set(by_ref) == {
        f"{config.fact_ref_prefix}{name}"
        for name in ("foundational", "trusted", "certified", "certification", "valid-skill-structure")
    }
    fact = by_ref["ai5-marketplace:default/valid-skill-structure"]
    assert fact["entityRef"] == config.entity_ref
    assert fact["data"]["check_id"] == "valid_skill_structure"
    assert fact["data"]["name"] == "Valid Skill Structure"
    assert fact["data"]["passed"] is validation_passed
    assert fact["data"]["score"] == float(validation_passed)
    assert fact["data"]["message"] == (
        "Skill structure validation passed" if validation_passed else "Skill structure validation failed"
    )
    assert fact["data"]["details"]["validator"] == "validate_skills_tier1"
    assert bool(fact["data"]["details"]["errors"]) is not validation_passed
    assert datetime.fromisoformat(fact["data"]["evaluated_at"]).tzinfo is not None
    nested = by_ref["ai5-marketplace:default/foundational"]["data"]["checks"]
    nested_structure = next(check for check in nested if check["check_id"] == "valid_skill_structure")
    assert nested_structure["passed"] is validation_passed
    request = urlopen.call_args_list[-1].args[0]
    assert request.full_url == config.endpoint
    assert request.get_header("Authorization") == "Bearer test-token"
    assert results[-1].fact_ref == fact["factRef"]
    assert results[-1].level == "valid-skill-structure"
    assert all(result.success for result in results)


def test_structure_push_failure_is_recorded(compass):
    config, urlopen = compass
    certification = compute_certification(gates=[], validation_passed=True)

    def respond(request, **kwargs):
        response = MagicMock()
        fact_ref = json.loads(request.data)["facts"][0]["factRef"]
        response.__enter__.return_value.status = 503 if fact_ref.endswith("valid-skill-structure") else 200
        return response

    urlopen.side_effect = respond

    results = push_certification_facts(certification, config)

    assert len(results) == 5
    assert all(result.success for result in results[:-1])
    assert results[-1].fact_ref == "ai5-marketplace:default/valid-skill-structure"
    assert results[-1].success is False
    assert results[-1].error == "Unexpected status 503"


@pytest.mark.parametrize("check_level", [None, "trusted", "certified"])
def test_structure_check_can_be_omitted_or_moved_by_policy(compass, check_level):
    config, urlopen = compass
    certification = compute_certification(gates=[], validation_passed=True)
    structure = next(
        check for check in certification.foundational.checks if check.check_id == CheckId.VALID_SKILL_STRUCTURE
    )
    certification.foundational.checks.remove(structure)
    if check_level:
        getattr(certification, check_level).checks.append(structure)

    results = push_certification_facts(certification, config)

    assert len(results) == urlopen.call_count == (5 if check_level else 4)
    if check_level:
        assert results[-1].fact_ref == "ai5-marketplace:default/valid-skill-structure"
