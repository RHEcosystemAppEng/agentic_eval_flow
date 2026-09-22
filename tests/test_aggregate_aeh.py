"""Tests for AEH report aggregation (scripts/aggregate_aeh.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from abevalflow.report import AnalysisResult
from scripts.aggregate_aeh import (
    _case_reward,
    _extract_mean_reward,
    _load_execution_metadata,
    _trials_from_per_case,
    aggregate_pairwise_run,
    aggregate_single_run,
)
from scripts.analyze import render_markdown


def _write_summary(run_dir: Path, summary: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.yaml").write_text(yaml.dump(summary))


class TestCaseReward:
    def test_boolean_pass(self):
        assert _case_reward({"exit_success": {"value": True}}) == 1.0

    def test_boolean_fail(self):
        assert _case_reward({"exit_success": {"value": False}}) == 0.0

    def test_numeric_mean(self):
        assert (
            _case_reward(
                {
                    "a": {"value": 0.5},
                    "b": {"value": 1.0},
                }
            )
            == 0.75
        )

    def test_top_level_reward(self):
        assert _case_reward({"reward": 0.9}) == 0.9

    def test_empty(self):
        assert _case_reward({}) is None

    def test_unit_scale_float_one_is_perfect(self):
        assert _case_reward({"quality": {"value": 1.0, "judge_type": "llm"}}) == 1.0

    def test_likert_one_is_floor_not_perfect(self):
        """YAML ``value: 1`` from a 1–5 int rubric is the worst bucket (Harbor 0.0)."""
        assert (
            _case_reward(
                {
                    "analysis_accuracy": {"value": 1, "judge_type": "llm"},
                    "response_received": {"value": True, "judge_type": "check"},
                }
            )
            == 0.0
        )

    def test_llm_error_does_not_use_boolean_as_quality(self):
        assert (
            _case_reward(
                {
                    "analysis_accuracy": {
                        "value": None,
                        "error": "No module named 'anthropic'",
                        "judge_type": "llm",
                    },
                    "response_received": {"value": True, "judge_type": "check"},
                }
            )
            is None
        )

    def test_llm_error_skipped_when_numeric_present(self):
        assert (
            _case_reward(
                {
                    "analysis_accuracy": {
                        "value": None,
                        "error": "No module named 'anthropic'",
                        "judge_type": "llm",
                    },
                    "analysis_citations": {"value": 5, "judge_type": "llm"},
                }
            )
            == 1.0
        )


class TestTrialsFromPerCase:
    def test_maps_case_ids(self):
        trials = _trials_from_per_case(
            {
                "case-001": {"exit_success": {"value": True}},
                "case-002": {"exit_success": {"value": False}},
            }
        )
        assert len(trials) == 2
        assert trials[0]["trial_name"] == "case-001"
        assert trials[0]["reward"] == 1.0
        assert trials[0]["judges"]["exit_success"]["value"] is True
        assert trials[1]["reward"] == 0.0


class TestAggregateSingleRun:
    def test_trials_match_n_trials(self, tmp_path: Path):
        run_dir = tmp_path / "aeh-hello" / "run-1"
        _write_summary(
            run_dir,
            {
                "run_id": "run-1",
                "mean_reward": 1.0,
                "per_case": {
                    "case-001": {"exit_success": {"value": True}},
                },
                "judges": {},
            },
        )
        report = aggregate_single_run(run_dir)
        assert report["summary"]["treatment"]["n_trials"] == 1
        assert len(report["trials"]["treatment"]) == 1
        assert report["trials"]["treatment"][0]["trial_name"] == "case-001"
        assert report["trials"]["treatment"][0]["judges"]["exit_success"]["value"] is True
        assert report["trials"]["control"] == []
        assert report["summary"]["control"]["mean_reward"] is None
        assert report["summary"]["mean_reward_gap"] is None
        assert report["mode"] == "single"

        result = AnalysisResult.model_validate(report)
        md = render_markdown(result)
        assert "not an A/B comparison" in md
        assert "Cases (1)" in md
        assert "case-001" in md
        assert "| Metric | Treatment | Control |" not in md
        assert "exit_success" in md


class TestAggregatePairwiseRun:
    def test_trials_and_pairwise_section(self, tmp_path: Path):
        treatment = tmp_path / "skill" / "treatment-1"
        control = tmp_path / "skill" / "control-1"
        _write_summary(
            treatment,
            {
                "run_id": "treatment-1",
                "mean_reward": 1.0,
                "per_case": {"case-001": {"exit_success": {"value": True}}},
                "pairwise": {
                    "run_a": "treatment-1",
                    "run_b": "control-1",
                    "cases_compared": 1,
                    "wins_a": 1,
                    "wins_b": 0,
                    "ties": 0,
                    "errors": 0,
                    "per_case": [{"case_id": "case-001", "winner": "a"}],
                },
            },
        )
        _write_summary(
            control,
            {
                "run_id": "control-1",
                "mean_reward": 0.0,
                "per_case": {"case-001": {"exit_success": {"value": False}}},
            },
        )
        report = aggregate_pairwise_run(treatment, control)
        assert len(report["trials"]["treatment"]) == 1
        assert len(report["trials"]["control"]) == 1
        assert report["pairwise"]["wins_a"] == 1
        assert report["aeh_warnings"] == []

        result = AnalysisResult.model_validate(report)
        md = render_markdown(result)
        assert "## Pairwise Comparison" in md
        assert "**Treatment wins:** 1" in md
        assert "case-001" in md
        assert "Treatment (1 trials)" in md
        assert "Control (1 trials)" in md

    def test_missing_pairwise_warns(self, tmp_path: Path):
        treatment = tmp_path / "skill" / "treatment-1"
        control = tmp_path / "skill" / "control-1"
        _write_summary(
            treatment,
            {
                "run_id": "treatment-1",
                "mean_reward": 0.0,
                "per_case": {},
            },
        )
        _write_summary(control, {"run_id": "control-1", "mean_reward": 0.0, "per_case": {}})
        report = aggregate_pairwise_run(treatment, control)
        assert report["aeh_warnings"]
        assert report["pairwise"]["cases_compared"] == 0

    def test_all_ties_is_pass_with_rewards_from_run_result(self, tmp_path: Path):
        treatment = tmp_path / "skill" / "treatment-1"
        control = tmp_path / "skill" / "control-1"
        _write_summary(
            treatment,
            {
                "run_id": "treatment-1",
                "per_case": {"case-001": {"exit_success": {"value": True}}},
                "pairwise": {
                    "wins_a": 0,
                    "wins_b": 0,
                    "ties": 1,
                    "errors": 0,
                    "cases_compared": 1,
                    "per_case": [{"case_id": "case-001", "winner": "tie"}],
                },
            },
        )
        (treatment / "run_result.json").write_text(json.dumps({"mean_reward": 1.0}))
        _write_summary(
            control,
            {
                "run_id": "control-1",
                "per_case": {"case-001": {"exit_success": {"value": True}}},
            },
        )
        (control / "run_result.json").write_text(json.dumps({"mean_reward": 1.0}))
        report = aggregate_pairwise_run(treatment, control)
        assert report["summary"]["treatment"]["mean_reward"] == 1.0
        assert report["summary"]["control"]["mean_reward"] == 1.0
        assert report["pairwise"]["win_rate"] == 0.0  # ties are non-wins
        assert report["recommendation"] == "pass"  # all-ties is still pass

    def test_errors_lower_win_rate(self, tmp_path: Path):
        treatment = tmp_path / "skill" / "treatment-1"
        control = tmp_path / "skill" / "control-1"
        _write_summary(
            treatment,
            {
                "run_id": "treatment-1",
                "per_case": {},
                "pairwise": {
                    "wins_a": 1,
                    "wins_b": 0,
                    "ties": 0,
                    "errors": 9,
                    "cases_compared": 10,
                    "per_case": [],
                },
            },
        )
        _write_summary(control, {"run_id": "control-1", "per_case": {}})
        report = aggregate_pairwise_run(treatment, control)
        assert report["pairwise"]["win_rate"] == pytest.approx(0.1)
        assert report["recommendation"] == "fail"

    def test_submission_name_override(self, tmp_path: Path):
        run_dir = tmp_path / "skill-from-path" / "run-1"
        _write_summary(
            run_dir,
            {
                "run_id": "run-1",
                "mean_reward": 1.0,
                "per_case": {"case-001": {"exit_success": {"value": True}}},
                "judges": {},
            },
        )
        report = aggregate_single_run(run_dir, submission_name="pipeline-submission")
        assert report["submission_name"] == "pipeline-submission"

    def test_likert_one_is_not_a_pass(self, tmp_path: Path):
        run_dir = tmp_path / "skill" / "run-1"
        _write_summary(
            run_dir,
            {
                "run_id": "run-1",
                "mean_reward": 0.2,
                "per_case": {
                    "case-001": {"output_quality": {"value": 1}},
                },
                "judges": {},
            },
        )
        report = aggregate_single_run(run_dir)
        assert report["passed_cases"] == 0
        assert report["pass_rate"] == 0.0


class TestLoadExecutionMetadata:
    def test_openshell_fields(self, tmp_path: Path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "run_result.json").write_text(
            json.dumps(
                {
                    "execution_mode": "openshell",
                    "agent": "openshell:openclaw",
                    "n_cases": 2,
                    "n_failed": 0,
                    "wall_clock_s": 277.1,
                    "token_usage": {"input": 10, "output": 5},
                    "cost_usd": None,
                    "per_case": {
                        "morning-briefing": {
                            "response_text": "THIS MUST NOT APPEAR IN EXECUTION METADATA " * 50,
                        }
                    },
                }
            )
        )
        meta = _load_execution_metadata(run_dir)
        assert meta["execution_mode"] == "openshell"
        assert meta["agent"] == "openshell:openclaw"
        assert meta["n_cases"] == 2
        assert meta["n_failed"] == 0
        assert meta["wall_clock_s"] == 277.1
        assert meta["duration_s"] == 277.1
        assert meta["tokens"] == {"input": 10, "output": 5}
        dumped = json.dumps(meta)
        assert "response_text" not in dumped
        assert "THIS MUST NOT" not in dumped

        (run_dir / "summary.yaml").write_text(
            yaml.dump({"run_id": "run", "mean_reward": 0.0, "per_case": {}, "judges": {}})
        )
        report = aggregate_single_run(run_dir, eval_engine="aeh_openshell_openclaw")
        assert report["eval_engine"] == "aeh_openshell_openclaw"
        assert report["execution"]["execution_mode"] == "openshell"
        result = AnalysisResult.model_validate(report)
        assert result.execution is not None
        assert result.execution["execution_mode"] == "openshell"

    def test_harbor_fields_unchanged(self, tmp_path: Path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "run_result.json").write_text(
            json.dumps(
                {
                    "execution_mode": "harbor",
                    "duration_s": 12.5,
                    "mean_reward": 0.8,
                    "harbor_job_dir": "/jobs/x",
                    "num_cases": 2,
                    "n_infra_errors": 0,
                    "n_trial_errors": 0,
                    "token_usage": {"input": 1, "output": 1},
                }
            )
        )
        meta = _load_execution_metadata(run_dir)
        assert meta["duration_s"] == 12.5
        assert meta["harbor_job_dir"] == "/jobs/x"
        assert meta["n_cases"] == 2
        assert meta["execution_mode"] == "harbor"


class TestExtractMeanReward:
    def test_llm_errors_are_not_silent_zero(self, tmp_path: Path):
        run_dir = tmp_path / "run"
        _write_summary(
            run_dir,
            {
                "run_id": "run-1",
                "mean_reward": 0.0,
                "per_case": {
                    "analysis-panel": {
                        "analysis_accuracy": {
                            "value": None,
                            "error": "No module named 'anthropic'",
                            "judge_type": "llm",
                        },
                        "response_received": {"value": True, "judge_type": "check"},
                    }
                },
                "judges": {
                    "analysis_accuracy": {"mean": None, "errored_cases": 1},
                    "response_received": {"mean": 1.0, "pass_rate": 1.0},
                },
            },
        )
        assert _extract_mean_reward(run_dir) is None
        report = aggregate_single_run(run_dir, eval_engine="aeh_openshell_openclaw")
        assert report["mean_reward"] is None
        assert report["summary"]["treatment"]["mean_reward"] is None
        assert report["summary"]["treatment"]["n_errors"] == 1
        assert report["summary"]["control"]["mean_reward"] is None
        assert report["summary"]["mean_reward_gap"] is None
        assert report["recommendation"] == "fail"
        judges = report["trials"]["treatment"][0]["judges"]
        assert judges["analysis_accuracy"]["error"]
        assert "rationale" not in judges["analysis_accuracy"]
        result = AnalysisResult.model_validate(report)
        assert result.summary.treatment.mean_reward is None
        assert result.summary.control.mean_reward is None
        assert result.summary.mean_reward_gap is None
        assert result.per_case["analysis-panel"]["analysis_accuracy"]["error"]
        assert result.trials["treatment"][0].judges["response_received"]["value"] is True
        md = render_markdown(result)
        assert "not an A/B comparison" in md
        assert "analysis_accuracy" in md
        assert "ERR" in md
        assert "scoring judges errored" in md or "judge error" in md.lower()

    def test_computes_from_per_case_when_mean_reward_omitted(self, tmp_path: Path):
        run_dir = tmp_path / "run"
        _write_summary(
            run_dir,
            {
                "run_id": "run-1",
                "per_case": {
                    "morning-briefing": {
                        "prioritization_recall": {"value": 5, "judge_type": "llm"},
                        "response_received": {"value": True, "judge_type": "check"},
                    }
                },
                "judges": {"prioritization_recall": {"mean": 5.0}},
            },
        )
        assert _extract_mean_reward(run_dir) == 1.0
        report = aggregate_single_run(run_dir)
        assert report["mean_reward"] == 1.0
        assert report["per_case"]["morning-briefing"]["prioritization_recall"]["value"] == 5

    def test_float_ones_without_mean_reward_pass(self, tmp_path: Path):
        run_dir = tmp_path / "run"
        _write_summary(
            run_dir,
            {
                "run_id": "run-1",
                "per_case": {
                    "morning-briefing": {
                        "quality": {"value": 1.0, "judge_type": "llm"},
                        "response_received": {"value": True, "judge_type": "check"},
                    }
                },
                "judges": {"quality": {"mean": 1.0, "pass_rate": None, "errored_cases": 0}},
            },
        )
        report = aggregate_single_run(run_dir)
        assert report["mean_reward"] == 1.0
        assert report["passed_cases"] == 1
        assert report["recommendation"] == "pass"

    def test_likert_floor_without_mean_reward_is_fail_not_perfect(self, tmp_path: Path):
        """vf668 shape: int 1 LLM scores + passing booleans, no top-level mean_reward.

        Harbor maps Likert 1/5 → reward 0.0. Treating those 1s as unit-scale
        1.0 would pass a run whose rationales say the briefing was empty.
        """
        run_dir = tmp_path / "run"
        _write_summary(
            run_dir,
            {
                "run_id": "aeh-openshell-openclaw-vf668",
                "per_case": {
                    "analysis-panel": {
                        "analysis_accuracy": {"value": 1, "judge_type": "llm"},
                        "analysis_citations": {"value": 1, "judge_type": "llm"},
                        "analysis_independent_judgment": {"value": 1, "judge_type": "llm"},
                        "response_received": {"value": True, "judge_type": "check"},
                        "used_m365_tools": {"value": True, "judge_type": "check"},
                    },
                    "morning-briefing": {
                        "connection_precision": {"value": 1, "judge_type": "llm"},
                        "connection_recall": {"value": 1, "judge_type": "llm"},
                        "prioritization_precision": {"value": 1, "judge_type": "llm"},
                        "prioritization_recall": {"value": 1, "judge_type": "llm"},
                        "prioritization_relevance": {"value": 1, "judge_type": "llm"},
                        "response_received": {"value": True, "judge_type": "check"},
                        "used_m365_tools": {"value": True, "judge_type": "check"},
                    },
                },
                "judges": {
                    "analysis_accuracy": {"mean": 1.0, "pass_rate": None, "errored_cases": 0},
                    "response_received": {"mean": 1.0, "pass_rate": 1.0, "errored_cases": 0},
                    "used_m365_tools": {"mean": 1.0, "pass_rate": 1.0, "errored_cases": 0},
                },
            },
        )
        assert _extract_mean_reward(run_dir) == 0.0
        report = aggregate_single_run(run_dir, eval_engine="aeh_openshell_openclaw")
        assert report["mean_reward"] == 0.0
        assert report["summary"]["treatment"]["n_passed"] == 0
        assert report["summary"]["treatment"]["n_failed"] == 2
        assert report["recommendation"] == "fail"
        assert report["trials"]["treatment"][0]["reward"] == 0.0
        result = AnalysisResult.model_validate(report)
        md = render_markdown(result)
        assert "1/5" in md
        assert "floor" in md.lower()
        assert "| analysis_accuracy | 1.00/5 |" in md
        assert "| response_received | 1.0000 | 100% |" in md
