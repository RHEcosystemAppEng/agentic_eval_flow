"""Configuration ownership, metadata compatibility, and secret handling."""

import json

import pytest

from abevalflow.config.runtime import ConfigurationError, resolve_config
from abevalflow.workflow.planning import build_plan


def test_submission_defaults_do_not_get_replaced_by_runtime_defaults(catalog):
    config = resolve_config({**catalog, "tiers": "1,2"}, {})
    assert config.metadata.aeh_mode.value == "pairwise"
    assert config.submission_review
    assert config.origins["submission_review"] == "metadata.skip_quality_review"
    assert build_plan(config)["steps"] == [
        "prepare",
        "structure",
        "security",
        "quality",
        "submission",
        "submission-review",
        "evaluate",
        "report",
    ]


def test_tier1_does_not_trigger_submission_review_from_available_keys(catalog):
    config = resolve_config(catalog, {"LLM_API_KEY": "secret"})
    assert config.tiers == (1,)
    assert not config.submission_review
    assert config.persist == ()


def test_discovery_preserves_catalog_layout(catalog):
    options = {k: v for k, v in catalog.items() if k != "submission"}
    config = resolve_config(options, {})
    assert config.submission == catalog["submission"]


def test_flags_override_environment_and_explicit_negative_boolean(catalog):
    config = resolve_config(
        {**catalog, "skill_review": False, "namespace": "flag-ns"},
        {"ABEVAL_SKILL_REVIEW": "true", "AGENT_EVAL_K8S_NAMESPACE": "env-ns"},
    )
    assert not config.skill_review
    assert config.namespace == "flag-ns"


def test_environment_only_configuration(catalog):
    env = {f"ABEVAL_{key.upper()}": str(value) for key, value in catalog.items()}
    env.update(ABEVAL_TIERS="2", ABEVAL_SUBMISSION_REVIEW="off", ABEVAL_PERSIST="postgres,s3")
    config = resolve_config({}, env)
    assert config.tiers == (2,)
    assert not config.submission_review
    assert config.persist == ("postgres", "s3")


@pytest.mark.parametrize("tiers", ["0", "1,3", "", "hello"])
def test_invalid_tiers_rejected(catalog, tiers):
    with pytest.raises(ConfigurationError):
        resolve_config({**catalog, "tiers": tiers}, {})


def test_no_submission_is_required_for_static_checks(tmp_path):
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Skill")
    config = resolve_config({"skill": skill, "output": tmp_path / "run"}, {})
    assert config.submission is None
    with pytest.raises(ConfigurationError, match="requires an AEH submission"):
        resolve_config({"skill": skill, "tiers": "2"}, {})


def test_skip_review_metadata_is_honored_and_can_be_overridden(catalog):
    (catalog["submission"] / "metadata.yaml").write_text("name: demo\nskip_quality_review: true\n")
    assert not resolve_config({**catalog, "tiers": "2"}, {}).submission_review
    assert resolve_config({**catalog, "tiers": "2", "submission_review": "on"}, {}).submission_review


def test_blocking_review_cannot_be_disabled(catalog):
    (catalog["submission"] / "metadata.yaml").write_text(
        "name: demo\nskip_quality_review: true\ngate_policy:\n  gates:\n    quality: {mode: block}\n"
    )
    with pytest.raises(ConfigurationError, match="requires submission review"):
        resolve_config({**catalog, "tiers": "2"}, {})


@pytest.mark.parametrize("field", ["llm_api_key", "agent_api_key", "database_url", "s3_access_key", "s3_secret_key"])
def test_plan_does_not_serialize_credentials(catalog, field):
    config = resolve_config({**catalog, field: "private-value"}, {})
    assert "private-value" not in json.dumps(build_plan(config))


def test_compass_inline_token_redacted(catalog):
    (catalog["submission"] / "metadata.yaml").write_text(
        "name: demo\ngate_policy:\n  push_facts:\n    endpoint: https://example.com\n"
        "    entity_ref: component:default/demo\n    bearer_token: private-value\n"
    )
    config = resolve_config(catalog, {})
    assert "private-value" not in json.dumps(build_plan(config))


@pytest.mark.parametrize(
    "field,value",
    [("output", "inside"), ("persist", "unknown"), ("submission_review", "unknown"), ("skill_review", "sometimes")],
)
def test_invalid_workflow_configuration(catalog, field, value):
    value = catalog["skill"] / "output" if value == "inside" else value
    with pytest.raises(ConfigurationError):
        resolve_config({**catalog, field: value}, {})


def test_invalid_metadata_is_not_replaced_by_defaults(catalog):
    (catalog["submission"] / "metadata.yaml").write_text("name: demo\nunknown: true\n")
    with pytest.raises(ConfigurationError, match="Invalid submission"):
        resolve_config(catalog, {})


def test_explicit_source_contains_all_inputs(catalog, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(ConfigurationError, match="Submission must be inside"):
        resolve_config({**catalog, "submission": outside}, {})


@pytest.mark.parametrize("runtime", ["podman", "docker"])
@pytest.mark.parametrize(
    "provided", [{}, {"security_image": "evaluation:check"}, {"quality_image": "evaluation:check"}]
)
def test_container_scanning_requires_explicit_images(catalog, runtime, provided):
    with pytest.raises(ConfigurationError, match="Container scanning requires"):
        resolve_config({**catalog, "scanner_runtime": runtime, **provided}, {})


def test_oci_is_not_an_application_persistence_destination(catalog):
    with pytest.raises(ConfigurationError, match="postgres,s3,compass"):
        resolve_config({**catalog, "persist": "oci"}, {})
