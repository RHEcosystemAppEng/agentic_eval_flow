"""Tests for AEH OpenShell OpenClaw validation in scripts/validate.py."""

from abevalflow.schemas import EvalEngine
from scripts.validate import validate_submission
from tests.test_validate_aeh import VALID_EVAL_YAML, create_aeh_submission


class TestOpenShellOpenClawValidation:
    """OpenShell engine requires runner.type=openclaw on top of AEH layout."""

    def test_happy_path_openclaw_runner(self, tmp_path):
        eval_yaml = {**VALID_EVAL_YAML, "runner": {"type": "openclaw"}}
        metadata = {
            "schema_version": "1.0",
            "name": "my-aeh-eval",
            "eval_engine": "aeh_openshell_openclaw",
        }
        create_aeh_submission(tmp_path, eval_yaml_content=eval_yaml, metadata_content=metadata)
        errors = validate_submission(tmp_path, eval_engine=EvalEngine.AEH_OPENSHELL_OPENCLAW)
        assert errors == []

    def test_missing_openclaw_runner_fails(self, tmp_path):
        eval_yaml = {**VALID_EVAL_YAML, "runner": {"type": "claude-code"}}
        create_aeh_submission(tmp_path, eval_yaml_content=eval_yaml)
        errors = validate_submission(tmp_path, eval_engine=EvalEngine.AEH_OPENSHELL_OPENCLAW)
        assert any("runner.type must be 'openclaw'" in e for e in errors)

    def test_missing_runner_block_fails(self, tmp_path):
        create_aeh_submission(tmp_path)
        errors = validate_submission(tmp_path, eval_engine=EvalEngine.AEH_OPENSHELL_OPENCLAW)
        assert any("runner.type must be 'openclaw'" in e for e in errors)

    def test_aeh_harbor_engine_does_not_require_openclaw(self, tmp_path):
        eval_yaml = {**VALID_EVAL_YAML, "runner": {"type": "claude-code"}}
        create_aeh_submission(tmp_path, eval_yaml_content=eval_yaml)
        errors = validate_submission(tmp_path, eval_engine=EvalEngine.AEH)
        assert errors == []

    def test_skill_must_match_metadata_name(self, tmp_path):
        eval_yaml = {
            **VALID_EVAL_YAML,
            "skill": "other-name",
            "runner": {"type": "openclaw"},
        }
        metadata = {
            "schema_version": "1.0",
            "name": "my-aeh-eval",
            "eval_engine": "aeh_openshell_openclaw",
        }
        create_aeh_submission(tmp_path, eval_yaml_content=eval_yaml, metadata_content=metadata)
        errors = validate_submission(tmp_path, eval_engine=EvalEngine.AEH_OPENSHELL_OPENCLAW)
        assert any("must match metadata.name" in e for e in errors)
