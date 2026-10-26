"""Unit tests for shared AEH scoring helpers."""

from __future__ import annotations

from pathlib import Path

import yaml

from abevalflow.aeh_scoring import (
    DEFAULT_AEH_THRESHOLD,
    case_reward_from_judges,
    load_judge_score_ranges,
    numeric_judge_is_low,
    numeric_judge_passes,
    pairwise_outcome,
    resolve_evaluation_threshold,
)


class TestPairwiseOutcome:
    def test_includes_errors_in_denominator(self):
        out = pairwise_outcome(wins_a=1, wins_b=0, ties=0, errors=9)
        assert out["total"] == 10
        assert out["win_rate"] == 0.1
        assert out["passed"] is False

    def test_all_ties_passes(self):
        out = pairwise_outcome(wins_a=0, wins_b=0, ties=5, errors=0)
        assert out["all_ties"] is True
        assert out["passed"] is True
        assert out["win_rate"] == 0.0

    def test_all_errors_fails(self):
        out = pairwise_outcome(wins_a=0, wins_b=0, ties=0, errors=5)
        assert out["all_ties"] is False
        assert out["passed"] is False


class TestNumericJudge:
    def test_normalized_scale(self):
        assert numeric_judge_passes(0.5) is True
        assert numeric_judge_passes(0.49) is False

    def test_likert_int(self):
        assert numeric_judge_passes(1) is False
        assert numeric_judge_passes(3) is True
        assert numeric_judge_is_low(2) is True

    def test_likert_float(self):
        assert numeric_judge_passes(2.5) is False
        assert numeric_judge_passes(3.0) is True


class TestCaseRewardFromJudges:
    """``case_reward_from_judges`` is the single shared source of truth for

    "did this case's own judges pass it" -- used by both the AEH result aggregator
    (report.json trials/summary) and the AEH engine's pairwise gate
    (grounded-disagreement detection), so the two can never again silently
    disagree about the same case's reward.
    """

    def test_passing_checks_gate_a_unit_scale_numeric_judge(self):
        """A unit-scale float (not a bare int -- see the ambiguity test below)

        is the clean, unambiguous case: all boolean checks pass, so the
        reward is exactly the numeric judge's own normalized value.
        """
        assert (
            case_reward_from_judges(
                {
                    "used_mcp_tool": {"value": True, "judge_type": "check"},
                    "validation_accuracy": {"value": 1.0, "judge_type": "llm"},
                    "wrote_report": {"value": True, "judge_type": "check"},
                }
            )
            == 1.0
        )

    def test_failing_llm_judge_with_passing_checks(self):
        """Passing bool checks don't average in -- they only gate; the numeric
        LLM judge alone determines the reward once all bools pass."""
        assert (
            case_reward_from_judges(
                {
                    "used_mcp_tool": {"value": True, "judge_type": "check"},
                    "validation_accuracy": {"value": 0, "judge_type": "llm"},
                    "wrote_report": {"value": True, "judge_type": "check"},
                }
            )
            == 0.0
        )

    def test_bare_int_one_is_ambiguous_without_declared_score_range(self):
        """Without a declared score_range, a bare ``value: 1`` from a judge

        declared ``score_range: [0, 1]`` (a perfect unit-scale score) is
        indistinguishable from a 1-5 Likert floor, because this function has
        no information about the judge's declared score_range. Both
        normalize to 0.0. This is the documented fallback heuristic, used
        only when the caller cannot supply ``score_ranges`` (see
        ``AEHEngine._pairwise_grounded_disagreement``, which avoids this by
        comparing each arm's already-resolved ``mean_reward`` instead).
        """
        assert (
            case_reward_from_judges(
                {
                    "used_mcp_tool": {"value": True, "judge_type": "check"},
                    "validation_accuracy": {"value": 1, "judge_type": "llm"},
                    "wrote_report": {"value": True, "judge_type": "check"},
                }
            )
            == 0.0
        )

    def test_bare_int_one_with_declared_unit_score_range_is_perfect(self):
        """A declared ``score_range: [0, 1]`` (read from eval.yaml via

        ``load_judge_score_ranges``) resolves the ambiguity above
        authoritatively: a bare ``value: 1`` is the ceiling (1.0), not a
        Likert floor. Regression test for the production bug where
        cve-validation's ``validation_accuracy`` judge (declared
        ``score_range: [0, 1]``) scored a perfect response but the report
        showed ``reward: 0.0`` for every trial.
        """
        assert (
            case_reward_from_judges(
                {
                    "used_mcp_tool": {"value": True, "judge_type": "check"},
                    "validation_accuracy": {"value": 1, "judge_type": "llm"},
                    "wrote_report": {"value": True, "judge_type": "check"},
                },
                score_ranges={"validation_accuracy": (0.0, 1.0)},
            )
            == 1.0
        )

    def test_declared_score_range_does_not_affect_unrelated_judges(self):
        """``score_ranges`` only disambiguates judges present in the mapping;

        a Likert judge not covered by it still falls back to the int/float
        heuristic.
        """
        assert (
            case_reward_from_judges(
                {
                    "validation_accuracy": {"value": 1, "judge_type": "llm"},
                    "other_likert_judge": {"value": 1, "judge_type": "llm"},
                },
                score_ranges={"validation_accuracy": (0.0, 1.0)},
            )
            == 0.5  # validation_accuracy -> 1.0 (declared unit scale), other_likert_judge -> 0.0 (Likert floor)
        )

    def test_empty_case_is_none(self):
        assert case_reward_from_judges({}) is None

    def test_non_dict_is_none(self):
        assert case_reward_from_judges(None) is None


class TestLoadJudgeScoreRanges:
    def test_missing_config_returns_empty(self, tmp_path: Path):
        assert load_judge_score_ranges(tmp_path / "missing.yaml") == {}

    def test_none_config_returns_empty(self):
        assert load_judge_score_ranges(None) == {}

    def test_reads_declared_score_ranges(self, tmp_path: Path):
        config = tmp_path / "eval-treatment.yaml"
        config.write_text(
            yaml.dump(
                {
                    "judges": [
                        {"name": "wrote_report", "type": "check"},
                        {"name": "validation_accuracy", "type": "llm", "score_range": [0, 1]},
                        {"name": "likert_judge", "type": "llm", "score_range": [1, 5]},
                    ]
                }
            )
        )
        assert load_judge_score_ranges(config) == {
            "validation_accuracy": (0.0, 1.0),
            "likert_judge": (1.0, 5.0),
        }

    def test_malformed_config_returns_empty(self, tmp_path: Path):
        config = tmp_path / "eval.yaml"
        config.write_text("not: [valid, yaml: structure")
        assert load_judge_score_ranges(config) == {}


class TestResolveEvaluationThreshold:
    def test_default_when_missing(self, tmp_path: Path):
        assert resolve_evaluation_threshold(tmp_path / "missing.yaml") == DEFAULT_AEH_THRESHOLD

    def test_reads_gate_policy(self, tmp_path: Path):
        meta = tmp_path / "metadata.yaml"
        meta.write_text(
            yaml.dump(
                {
                    "name": "demo",
                    "gate_policy": {"gates": {"evaluation": {"threshold": 0.6}}},
                }
            )
        )
        assert resolve_evaluation_threshold(meta) == 0.6
