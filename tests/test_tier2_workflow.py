"""AEH workflow integration using controlled engine and review boundaries."""

from unittest.mock import MagicMock, patch

import pytest
import yaml

from abevalflow.config.runtime import resolve_config
from abevalflow.workflow.service import run


def prepare_submission(catalog, mode="single", skip_review=True):
    submission = catalog["submission"]
    (submission / "metadata.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "aeh_mode": mode,
                "skip_quality_review": skip_review,
            }
        )
    )
    config = {
        "models": {"skill": "agent-model", "judge": "judge-model"},
        "judges": [{"name": "pairwise", "type": "llm", "prompt": "Compare {{ annotations }}"}],
        "outputs": [{"path": "output"}],
    }
    for filename in ["eval.yaml", "eval-control.yaml", "eval-treatment.yaml"]:
        (submission / filename).write_text(yaml.safe_dump(config))
    case = submission / "cases/case-001"
    case.mkdir(parents=True)
    (case / "input.yaml").write_text("prompt: Solve the example\n")


def write_evaluation(directory, *, pairwise=False):
    directory.mkdir(parents=True, exist_ok=True)
    summary = {
        "mean_reward": 0.9,
        "judges": {"correctness": {"mean": 0.9, "pass_rate": 1.0}},
        "per_case": {"case-001": {"correctness": {"value": 0.9}}},
    }
    if pairwise:
        summary["pairwise"] = {"wins_a": 1, "wins_b": 0, "ties": 0, "errors": 0, "cases_compared": 1}
    (directory / "summary.yaml").write_text(yaml.safe_dump(summary))


@pytest.mark.parametrize("mode", ["single", "pairwise"])
def test_tier2_orchestration_aggregates_real_report_contract(catalog, mode):
    prepare_submission(catalog, mode)
    config = resolve_config({**catalog, "tiers": "2", "trial_image": "trial-image"}, {})
    runner = MagicMock()

    def single(evaluation, output, run_id, **kwargs):
        write_evaluation(output)
        return 0

    def pairwise(control, treatment, reports, run_id, **kwargs):
        control_output = reports / "demo" / f"control-{run_id}"
        treatment_output = reports / "demo" / f"treatment-{run_id}"
        write_evaluation(control_output)
        write_evaluation(treatment_output, pairwise=True)
        return {
            "control_dir": str(control_output),
            "treatment_dir": str(treatment_output),
            "control_exit": 0,
            "treatment_exit": 0,
            "pairwise_exit": 0,
        }

    runner.run_single.side_effect = single
    runner.run_pairwise.side_effect = pairwise
    with patch("abevalflow.execution.runner.HarborRunner", return_value=runner) as constructor:
        result = run(config)
    assert result.exit_code == 0, result.model_dump_json()
    assert result.evidence_complete
    assert next(s for s in result.steps if s.name == "evaluate").status == "completed"
    assert (config.output / "reports/report.md").is_file()
    assert constructor.call_args.kwargs["model"] == "agent-model"
    assert constructor.call_args.kwargs["judge_model"] == "judge-model"
    assert constructor.call_args.kwargs["environment"]["AGENT_EVAL_K8S_CREDENTIALS_SECRET"] == "llm-credentials"


def test_submission_review_is_default_on_for_tier2(catalog):
    prepare_submission(catalog, skip_review=False)
    config = resolve_config({**catalog, "tiers": "2"}, {})
    review = MagicMock()
    review.review_submission.return_value = {
        "recommendation": "warn",
        "overall_score": 0.5,
        "token_usage": {"prompt_tokens": 4, "completion_tokens": 2, "model": "review-model"},
    }
    with (
        patch("abevalflow.review.service.ReviewService", return_value=review),
        patch("abevalflow.execution.runner.HarborRunner", side_effect=RuntimeError("engine unavailable")),
    ):
        result = run(config)
    review.review_submission.assert_called_once()
    assert next(s for s in result.steps if s.name == "submission-review").verdict == "warn"
    assert (config.output / "reports/_ai_review.json").is_file()
    assert result.exit_code == 3
    assert next(s for s in result.steps if s.name == "report").status == "completed"


def test_advisory_review_error_does_not_prevent_agent_execution(catalog):
    prepare_submission(catalog, skip_review=False)
    config = resolve_config({**catalog, "tiers": "2"}, {})
    runner = MagicMock()

    def execute(evaluation, output, run_id, **kwargs):
        write_evaluation(output)
        return 0

    runner.run_single.side_effect = execute
    with (
        patch("abevalflow.review.service.ReviewService", side_effect=ValueError("review unavailable")),
        patch("abevalflow.execution.runner.HarborRunner", return_value=runner),
    ):
        result = run(config)
    assert result.exit_code == 0
    assert next(s for s in result.steps if s.name == "submission-review").status == "error"
    assert next(s for s in result.steps if s.name == "evaluate").status == "completed"
    assert not result.evidence_complete


def test_aehruntime_failure_is_not_a_quality_failure(catalog):
    prepare_submission(catalog)
    config = resolve_config({**catalog, "tiers": "2"}, {})
    runner = MagicMock()

    def execute(evaluation, output, run_id, **kwargs):
        write_evaluation(output)
        return 5

    runner.run_single.side_effect = execute
    with patch("abevalflow.execution.runner.HarborRunner", return_value=runner):
        result = run(config)
    step = next(s for s in result.steps if s.name == "evaluate")
    assert step.status == "error"
    assert step.verdict == "unavailable"
    assert result.exit_code == 3
