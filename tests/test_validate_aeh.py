"""Tests for AEH (Agent-Eval-Harness) validation in submission validation."""

from pathlib import Path

import yaml

from abevalflow.schemas import EvalEngine
from abevalflow.validation.submission import validate_submission

VALID_METADATA = {
    "schema_version": "1.0",
    "name": "my-aeh-eval",
    "eval_engine": "aeh",
}

VALID_EVAL_YAML = {
    "models": {"skill": "claude-sonnet-4-5", "judge": "claude-opus-4-6"},
    "judges": [
        # Ground truth: {{ annotations }} satisfies the pairwise grounding
        # check in _check_aeh_pairwise_contract for submissions whose cases
        # have annotations.yaml (the default in create_aeh_submission below).
        {"name": "correctness", "type": "llm", "prompt": "Is this correct? Ground truth: {{ annotations }}"},
    ],
    "thresholds": {"correctness": 0.7},
    # Pairwise score.py needs case artifact paths under cases/<id>/<path>/
    "outputs": [{"path": "output"}],
}

VALID_INPUT_YAML = {
    "prompt": "Write a hello world program",
    "context": {"language": "python"},
}


def create_aeh_submission(
    tmp_path: Path,
    *,
    include_metadata: bool = True,
    include_eval_yaml: bool = True,
    include_cases: bool = True,
    eval_yaml_content: dict | None = None,
    metadata_content: dict | None = None,
    case_names: list[str] | None = None,
) -> Path:
    """Create a minimal AEH submission directory."""
    if include_metadata:
        content = metadata_content or VALID_METADATA
        (tmp_path / "metadata.yaml").write_text(yaml.dump(content))

    if include_eval_yaml:
        content = eval_yaml_content or VALID_EVAL_YAML
        (tmp_path / "eval.yaml").write_text(yaml.dump(content))

    if include_cases:
        cases_dir = tmp_path / "cases"
        cases_dir.mkdir()
        for case_name in case_names or ["case-001"]:
            case_dir = cases_dir / case_name
            case_dir.mkdir()
            (case_dir / "input.yaml").write_text(yaml.dump(VALID_INPUT_YAML))
            (case_dir / "annotations.yaml").write_text(yaml.dump({"expected": "OK"}))

    return tmp_path


class TestAEHValidation:
    """Tests for AEH submission validation."""

    def test_valid_aeh_submission(self, tmp_path):
        """A valid AEH submission should pass validation."""
        submission = create_aeh_submission(tmp_path)
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert errors == []

    def test_skill_must_match_metadata_name(self, tmp_path):
        """skill field must match metadata.name so analyze finds report.json."""
        eval_yaml = {**VALID_EVAL_YAML, "skill": "other-name"}
        create_aeh_submission(tmp_path, eval_yaml_content=eval_yaml)
        errors = validate_submission(tmp_path, eval_engine=EvalEngine.AEH)
        assert any("must match metadata.name" in e for e in errors)

    def test_missing_eval_yaml(self, tmp_path):
        """eval.yaml is required for AEH."""
        submission = create_aeh_submission(tmp_path, include_eval_yaml=False)
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert any("eval.yaml is required" in e for e in errors)

    def test_missing_cases_dir(self, tmp_path):
        """cases/ directory is required for AEH."""
        submission = create_aeh_submission(tmp_path, include_cases=False)
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert any("cases/ directory is required" in e for e in errors)

    def test_empty_cases_dir(self, tmp_path):
        """cases/ must contain at least one case."""
        submission = create_aeh_submission(tmp_path, include_cases=False)
        (submission / "cases").mkdir()  # Empty cases dir
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert any("at least one case directory" in e for e in errors)

    def test_case_missing_input_yaml(self, tmp_path):
        """Each case must have input.yaml."""
        submission = create_aeh_submission(tmp_path, include_cases=False)
        cases_dir = submission / "cases"
        cases_dir.mkdir()
        (cases_dir / "case-001").mkdir()
        # No input.yaml in case-001
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert any("missing required input.yaml" in e for e in errors)

    def test_eval_yaml_missing_models(self, tmp_path):
        """eval.yaml must have models section."""
        bad_eval = {"judges": [{"name": "test"}]}
        submission = create_aeh_submission(tmp_path, eval_yaml_content=bad_eval)
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert any("'models' section is required" in e for e in errors)

    def test_eval_yaml_missing_models_skill(self, tmp_path):
        """eval.yaml models must have skill key."""
        bad_eval = {"models": {"judge": "claude-opus-4-6"}, "judges": []}
        submission = create_aeh_submission(tmp_path, eval_yaml_content=bad_eval)
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert any("'models.skill' is required" in e for e in errors)

    def test_eval_yaml_missing_judges(self, tmp_path):
        """eval.yaml must have judges section."""
        bad_eval = {"models": {"skill": "claude-sonnet-4-5"}}
        submission = create_aeh_submission(tmp_path, eval_yaml_content=bad_eval)
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert any("'judges' section is required" in e for e in errors)

    def test_eval_yaml_models_string_format(self, tmp_path):
        """eval.yaml can have models as a string (shorthand)."""
        eval_yaml = {"models": "claude-sonnet-4-5", "judges": []}
        submission = create_aeh_submission(tmp_path, eval_yaml_content=eval_yaml)
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        # Should not error on models being a string
        assert not any("models" in e.lower() for e in errors)

    def test_eval_yaml_invalid_yaml(self, tmp_path):
        """Invalid YAML in eval.yaml should be caught."""
        submission = create_aeh_submission(tmp_path, include_eval_yaml=False)
        (submission / "eval.yaml").write_text("invalid: yaml: content: [")
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert any("not valid YAML" in e for e in errors)

    def test_case_invalid_input_yaml(self, tmp_path):
        """Invalid YAML in input.yaml should be caught."""
        submission = create_aeh_submission(tmp_path, include_cases=False)
        cases_dir = submission / "cases"
        cases_dir.mkdir()
        case_dir = cases_dir / "case-001"
        case_dir.mkdir()
        (case_dir / "input.yaml").write_text("invalid: [yaml")
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert any("invalid YAML" in e for e in errors)

    def test_multiple_cases(self, tmp_path):
        """Multiple cases should all be validated."""
        submission = create_aeh_submission(tmp_path, case_names=["case-001", "case-002", "case-003"])
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert errors == []

    def test_aeh_does_not_require_skills_dir(self, tmp_path):
        """AEH submissions don't require skills/ directory."""
        submission = create_aeh_submission(tmp_path)
        # No skills/ directory
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert not any("skills/" in e for e in errors)

    def test_aeh_does_not_require_instruction_md(self, tmp_path):
        """AEH submissions don't require instruction.md."""
        submission = create_aeh_submission(tmp_path)
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert not any("instruction.md" in e for e in errors)

    def test_aeh_does_not_require_test_outputs(self, tmp_path):
        """AEH submissions don't require tests/test_outputs.py."""
        submission = create_aeh_submission(tmp_path)
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert not any("test_outputs.py" in e for e in errors)


class TestAEHPairwiseValidation:
    """Tests for AEH pairwise mode validation."""

    def test_pairwise_valid_submission(self, tmp_path):
        """A valid pairwise submission has both control and treatment configs."""
        submission = create_aeh_submission(tmp_path, include_eval_yaml=False)
        # Create both config files
        (submission / "eval-control.yaml").write_text(yaml.dump(VALID_EVAL_YAML))
        (submission / "eval-treatment.yaml").write_text(yaml.dump(VALID_EVAL_YAML))

        errors = validate_submission(
            submission,
            eval_engine=EvalEngine.AEH,
            aeh_mode="pairwise",
        )
        assert errors == []

    def test_pairwise_missing_control_config(self, tmp_path):
        """Pairwise mode requires eval-control.yaml."""
        submission = create_aeh_submission(tmp_path, include_eval_yaml=False)
        (submission / "eval-treatment.yaml").write_text(yaml.dump(VALID_EVAL_YAML))

        errors = validate_submission(
            submission,
            eval_engine=EvalEngine.AEH,
            aeh_mode="pairwise",
        )
        assert any("missing eval-control.yaml" in e for e in errors)

    def test_pairwise_missing_treatment_config(self, tmp_path):
        """Pairwise mode requires eval-treatment.yaml."""
        submission = create_aeh_submission(tmp_path, include_eval_yaml=False)
        (submission / "eval-control.yaml").write_text(yaml.dump(VALID_EVAL_YAML))

        errors = validate_submission(
            submission,
            eval_engine=EvalEngine.AEH,
            aeh_mode="pairwise",
        )
        assert any("missing eval-treatment.yaml" in e for e in errors)

    def test_pairwise_invalid_control_config(self, tmp_path):
        """Control config must have valid AEH structure."""
        submission = create_aeh_submission(tmp_path, include_eval_yaml=False)
        # Missing models.skill
        bad_config = {"models": {"judge": "claude-opus"}, "judges": []}
        (submission / "eval-control.yaml").write_text(yaml.dump(bad_config))
        (submission / "eval-treatment.yaml").write_text(yaml.dump(VALID_EVAL_YAML))

        errors = validate_submission(
            submission,
            eval_engine=EvalEngine.AEH,
            aeh_mode="pairwise",
        )
        assert any("eval-control.yaml" in e and "models.skill" in e for e in errors)

    def test_pairwise_requires_outputs(self, tmp_path):
        """Treatment config must declare outputs: for pairwise artifact comparison."""
        submission = create_aeh_submission(tmp_path, include_eval_yaml=False)
        no_outputs = {
            "models": {"skill": "claude-sonnet", "judge": "claude-sonnet"},
            "judges": [{"name": "pairwise", "type": "llm", "prompt": "Who wins?"}],
        }
        (submission / "eval-control.yaml").write_text(yaml.dump(VALID_EVAL_YAML))
        (submission / "eval-treatment.yaml").write_text(yaml.dump(no_outputs))
        errors = validate_submission(
            submission,
            eval_engine=EvalEngine.AEH,
            aeh_mode="pairwise",
        )
        assert any("outputs" in e for e in errors)

    def test_pairwise_custom_config_filenames(self, tmp_path):
        """Custom config filenames should be respected."""
        submission = create_aeh_submission(tmp_path, include_eval_yaml=False)
        (submission / "baseline.yaml").write_text(yaml.dump(VALID_EVAL_YAML))
        (submission / "variant.yaml").write_text(yaml.dump(VALID_EVAL_YAML))

        errors = validate_submission(
            submission,
            eval_engine=EvalEngine.AEH,
            aeh_mode="pairwise",
            aeh_control_config="baseline.yaml",
            aeh_treatment_config="variant.yaml",
        )
        assert errors == []

    def test_pairwise_still_requires_cases(self, tmp_path):
        """Pairwise mode still requires cases/ directory."""
        submission = create_aeh_submission(tmp_path, include_eval_yaml=False, include_cases=False)
        (submission / "eval-control.yaml").write_text(yaml.dump(VALID_EVAL_YAML))
        (submission / "eval-treatment.yaml").write_text(yaml.dump(VALID_EVAL_YAML))

        errors = validate_submission(
            submission,
            eval_engine=EvalEngine.AEH,
            aeh_mode="pairwise",
        )
        assert any("cases/ directory is required" in e for e in errors)

    def test_pairwise_judge_must_reference_annotations_when_cases_have_ground_truth(self, tmp_path):
        """Regression test: a pairwise judge blind to ground truth should fail validation.

        ``create_aeh_submission`` gives every case an ``annotations.yaml`` by
        default. A pairwise judge prompt that never references
        ``{{ annotations }}`` can only compare the two output artifacts on
        general plausibility, so for a case whose documented correct answer
        contradicts what a model would guess from training-data priors, it
        can side with the wrong answer and still gate the whole submission --
        exactly the production failure mode this check exists to catch
        before a submission reaches that stage.
        """
        submission = create_aeh_submission(tmp_path, include_eval_yaml=False)
        ungrounded = {
            "models": {"skill": "claude-sonnet", "judge": "claude-sonnet"},
            "judges": [{"name": "pairwise", "type": "llm", "prompt": "Which output is better, A or B?"}],
            "outputs": [{"path": "output"}],
        }
        (submission / "eval-control.yaml").write_text(yaml.dump(VALID_EVAL_YAML))
        (submission / "eval-treatment.yaml").write_text(yaml.dump(ungrounded))

        errors = validate_submission(submission, eval_engine=EvalEngine.AEH, aeh_mode="pairwise")

        assert any("does not reference '{{ annotations }}'" in e for e in errors)

    def test_pairwise_judge_grounded_in_annotations_passes(self, tmp_path):
        """A pairwise judge whose prompt references {{ annotations }} is accepted."""
        submission = create_aeh_submission(tmp_path, include_eval_yaml=False)
        grounded = {
            "models": {"skill": "claude-sonnet", "judge": "claude-sonnet"},
            "judges": [
                {
                    "name": "pairwise",
                    "type": "llm",
                    "prompt": "Ground truth: {{ annotations }}. Which output is better, A or B?",
                }
            ],
            "outputs": [{"path": "output"}],
        }
        (submission / "eval-control.yaml").write_text(yaml.dump(VALID_EVAL_YAML))
        (submission / "eval-treatment.yaml").write_text(yaml.dump(grounded))

        errors = validate_submission(submission, eval_engine=EvalEngine.AEH, aeh_mode="pairwise")

        assert not any("{{ annotations }}" in e for e in errors)

    def test_pairwise_judge_ungrounded_ok_without_case_annotations(self, tmp_path):
        """No grounding is required when no case has ground truth to ground against."""
        submission = create_aeh_submission(tmp_path, include_eval_yaml=False, include_cases=False)
        cases_dir = submission / "cases"
        cases_dir.mkdir()
        case_dir = cases_dir / "case-001"
        case_dir.mkdir()
        (case_dir / "input.yaml").write_text(yaml.dump(VALID_INPUT_YAML))
        # Deliberately no annotations.yaml in this case.
        ungrounded = {
            "models": {"skill": "claude-sonnet", "judge": "claude-sonnet"},
            "judges": [{"name": "pairwise", "type": "llm", "prompt": "Which output is better, A or B?"}],
            "outputs": [{"path": "output"}],
        }
        (submission / "eval-control.yaml").write_text(yaml.dump(VALID_EVAL_YAML))
        (submission / "eval-treatment.yaml").write_text(yaml.dump(ungrounded))

        errors = validate_submission(submission, eval_engine=EvalEngine.AEH, aeh_mode="pairwise")

        assert not any("annotations" in e for e in errors)

    def test_single_mode_ignores_pairwise_configs(self, tmp_path):
        """Single mode should look for eval.yaml, not pairwise configs."""
        submission = create_aeh_submission(tmp_path, include_eval_yaml=True)
        # Single mode should work with just eval.yaml
        errors = validate_submission(
            submission,
            eval_engine=EvalEngine.AEH,
            aeh_mode="single",
        )
        assert errors == []


class TestAEHPluginDirsValidation:
    """runner.plugin_dirs must exist and contain SKILL.md."""

    def test_missing_plugin_dir(self, tmp_path):
        eval_yaml = {
            **VALID_EVAL_YAML,
            "skill": "my-aeh-eval",
            "runner": {"plugin_dirs": ["skills"]},
        }
        create_aeh_submission(tmp_path, eval_yaml_content=eval_yaml)
        errors = validate_submission(tmp_path, eval_engine=EvalEngine.AEH)
        assert any("plugin_dirs entry 'skills/' is missing" in e for e in errors)

    def test_plugin_dir_without_skill_md(self, tmp_path):
        eval_yaml = {
            **VALID_EVAL_YAML,
            "skill": "my-aeh-eval",
            "runner": {"plugin_dirs": ["skills"]},
        }
        create_aeh_submission(tmp_path, eval_yaml_content=eval_yaml)
        (tmp_path / "skills").mkdir()
        errors = validate_submission(tmp_path, eval_engine=EvalEngine.AEH)
        assert any("must contain SKILL.md" in e for e in errors)

    def test_nested_skill_md_passes(self, tmp_path):
        eval_yaml = {
            **VALID_EVAL_YAML,
            "skill": "my-aeh-eval",
            "runner": {"plugin_dirs": ["skills"]},
        }
        create_aeh_submission(tmp_path, eval_yaml_content=eval_yaml)
        skill_dir = tmp_path / "skills" / "my-aeh-eval"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# skill\n")
        errors = validate_submission(tmp_path, eval_engine=EvalEngine.AEH)
        assert errors == []


class TestAEHToolInterceptionValidation:
    """inputs.tools / permissions / tool_handlers.yaml checks."""

    def test_tools_require_match_and_handlers_file(self, tmp_path):
        eval_yaml = {
            **VALID_EVAL_YAML,
            "inputs": {"tools": [{"prompt": "deny mcp"}]},  # missing match
        }
        create_aeh_submission(tmp_path, eval_yaml_content=eval_yaml)
        errors = validate_submission(tmp_path, eval_engine=EvalEngine.AEH)
        assert any("requires a non-empty 'match'" in e for e in errors)
        assert any("tool_handlers.yaml is missing" in e for e in errors)

    def test_tools_with_handlers_passes(self, tmp_path):
        eval_yaml = {
            **VALID_EVAL_YAML,
            "inputs": {"tools": [{"match": "Block MCP", "prompt": "deny"}]},
            "permissions": {"deny": ["mcp__*"]},
        }
        create_aeh_submission(tmp_path, eval_yaml_content=eval_yaml)
        (tmp_path / "tool_handlers.yaml").write_text(
            yaml.dump({"handlers": [{"patterns": ["mcp__*"], "action": "deny"}]})
        )
        errors = validate_submission(tmp_path, eval_engine=EvalEngine.AEH)
        assert errors == []

    def test_invalid_permissions_type(self, tmp_path):
        eval_yaml = {**VALID_EVAL_YAML, "permissions": ["mcp__*"]}
        create_aeh_submission(tmp_path, eval_yaml_content=eval_yaml)
        errors = validate_submission(tmp_path, eval_engine=EvalEngine.AEH)
        assert any("'permissions' must be a mapping" in e for e in errors)


class TestAEHEvalEngineEnum:
    """Tests for AEH in EvalEngine enum."""

    def test_aeh_in_enum(self):
        assert EvalEngine.AEH == "aeh"

    def test_validate_with_aeh_engine(self, tmp_path):
        """validate_submission accepts EvalEngine.AEH."""
        submission = create_aeh_submission(tmp_path)
        # Should not raise
        errors = validate_submission(submission, eval_engine=EvalEngine.AEH)
        assert isinstance(errors, list)
