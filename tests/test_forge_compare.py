import importlib

import pytest


def compare(rows):
    try:
        fn = importlib.import_module("abevalflow.forge_compare").compare
    except ImportError:
        pytest.fail("paired Forge comparison gate is missing")
    return fn(rows)


def rows():
    return [
        {
            "case": "revise",
            "pair": str(n),
            "provenance": {
                "image": "sha256:a",
                "model": "glm",
                "fixture": "abc",
                "runtime": "x",
                "prompt": "p",
                "clock": "fixed",
            },
            "baseline": {
                "valid": True,
                "usage_complete": True,
                "output_tokens": 100,
                "quality": 0,
                "critical_pass": False,
                "skill": "main",
            },
            "candidate": {
                "valid": True,
                "usage_complete": True,
                "output_tokens": 90,
                "quality": 1,
                "critical_pass": True,
                "skill": "candidate",
            },
        }
        for n in range(3)
    ]


def test_improvement_requires_correctness_and_no_output_increase():
    assert compare(rows())["verdict"] == "improved"
    x = rows()
    x[0]["candidate"]["critical_pass"] = False
    assert compare(x)["verdict"] == "quality_failed"
    x = rows()
    x[0]["candidate"]["output_tokens"] = 150
    assert compare(x)["verdict"] == "token_regression"


def test_missing_usage_or_duplicate_pairs_invalidate_comparison():
    x = rows()
    x[1]["baseline"]["usage_complete"] = False
    assert compare(x)["verdict"] == "invalid_eval"
    x = rows()
    x.append(x[0])
    assert compare(x)["verdict"] == "invalid_eval"


def test_shorter_incomplete_answer_cannot_win():
    x = rows()
    for r in x:
        r["candidate"].update(output_tokens=1, critical_pass=False)
    assert compare(x)["verdict"] == "quality_failed"


def test_small_sample_is_inconclusive_and_zero_baseline_has_no_ratio():
    assert compare(rows()[:1])["verdict"] == "inconclusive"
    x = rows()
    for r in x:
        r["baseline"]["output_tokens"] = r["candidate"]["output_tokens"] = 0
    assert compare(x)["output_tokens"]["percent_change"] is None


def test_per_case_regression_cannot_be_hidden_by_other_cases():
    x = rows()
    for r in x:
        r["candidate"]["output_tokens"] = 1
    for n in range(3):
        r = rows()[n]
        r["case"] = "new"
        r["candidate"]["output_tokens"] = 101
        x.append(r)
    assert compare(x)["verdict"] == "token_regression"


def test_mixed_provenance_and_nonfinite_usage_are_invalid():
    x = rows()
    x[0]["provenance"]["model"] = "different"
    assert compare(x)["verdict"] == "invalid_eval"
    x = rows()
    x[0]["candidate"]["output_tokens"] = float("nan")
    assert compare(x)["verdict"] == "invalid_eval"
