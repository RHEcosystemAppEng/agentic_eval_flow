"""Profile-local numeric reporting must not rename partial credit full success."""

from copy import deepcopy

import pytest

from abevalflow.harbor_extensions.scoring_report import build_scoring_summary, render_scoring_note


def assessment(task_checks, protocol_checks, judgment):
    def category(checks):
        score = sum(checks) / len(checks)
        return {"score": score, "passed": score == 1, "checks": {str(i): v for i, v in enumerate(checks)}}

    task, protocol = category(task_checks), category(protocol_checks)
    overall = (task["score"] + protocol["score"] + judgment) / 3
    return {
        "scoring_version": 2,
        "task_completion": task,
        "protocol_compliance": protocol,
        "llm_judgment": {
            "scoring_version": 2,
            "score": judgment,
            "passed": judgment == 1,
            "reason": "Grounded review",
            "findings": []
            if judgment == 1
            else [{"issue": "Unsupported claim", "evidence": "Trace event", "deduction": 1 - judgment}],
        },
        "weights": {key: 1 / 3 for key in ("task_completion", "protocol_compliance", "llm_judgment")},
        "overall_score": overall,
        "overall_passed": overall == 1,
        "failures": [],
    }


def pair(entry):
    return {"control": [deepcopy(entry)], "treatment": [deepcopy(entry)]}


def test_fractional_reward_and_full_credit_are_distinct():
    partial = assessment([True, True], [True, False], 0.75)
    full = assessment([True], [True], 1)
    inputs = {"control": [partial], "treatment": [partial, full]}
    original = deepcopy(inputs)
    result = build_scoring_summary(inputs)
    assert inputs == original
    assert result["variants"]["control"]["mean_overall_score"] == pytest.approx(0.75)
    treatment = result["variants"]["treatment"]
    assert treatment["mean_overall_score"] == pytest.approx(0.875)
    assert treatment["n_full_credit"] == 1
    assert treatment["full_credit_rate"] == 0.5
    assert treatment["dimension_means"]["protocol_compliance"] == 0.75
    assert treatment["trials"][0]["full_credit"] is False
    text = render_scoring_note(result)
    assert "reward > 0" in text and "not full success" in text
    assert "mean overall 0.875" in text and "full credit 1/2" in text
    assert "statistical skill benefit" in text


def test_valid_zero_is_scored_and_has_no_full_credit():
    result = build_scoring_summary(pair(assessment([False], [False], 0)))
    assert result["variants"]["control"]["n_scored"] == 1
    assert result["variants"]["control"]["mean_overall_score"] == 0
    assert result["variants"]["control"]["n_full_credit"] == 0


@pytest.mark.parametrize("bad", [None, True, float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_numeric_judgment_never_becomes_scored_zero(bad):
    entry = assessment([True], [True], 0.75)
    entry["llm_judgment"]["score"] = bad
    with pytest.raises(RuntimeError):
        build_scoring_summary(pair(entry))


def test_inconsistent_formula_or_version_rejected():
    entry = assessment([True], [True], 0.75)
    entry["overall_score"] = 1
    with pytest.raises(RuntimeError, match="inconsistent Harbor score"):
        build_scoring_summary(pair(entry))
    entry["scoring_version"] = 1
    with pytest.raises(RuntimeError, match="scoring_version"):
        build_scoring_summary(pair(entry))


@pytest.mark.parametrize("entries", [{}, {"control": []}, {"control": [], "treatment": []}])
def test_missing_variant_or_assessment_is_not_a_zero(entries):
    with pytest.raises(RuntimeError):
        build_scoring_summary(entries)
