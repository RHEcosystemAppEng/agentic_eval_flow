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


def test_regression_policy_accepts_unchanged_results_but_not_invalid_measurements():
    from abevalflow.forge_compare import compare as compare_policy

    x = rows()
    for row in x:
        row["baseline"].update(quality=1, critical_pass=True, output_tokens=90)
    assert compare_policy(x, policy="regression")["verdict"] == "passed"
    assert compare_policy(x, policy="improvement")["verdict"] == "no_quality_improvement"
    x[0]["candidate"]["output_tokens"] = 100
    assert compare_policy(x, policy="regression")["verdict"] == "token_regression"
    x[0]["candidate"]["usage_complete"] = False
    assert compare_policy(x, policy="regression")["verdict"] == "invalid_eval"
    x = rows()
    x[0]["candidate"]["critical_pass"] = False
    assert compare_policy(x, policy="regression")["verdict"] == "quality_failed"
    x = rows()
    del x[0]["candidate"]["quality"]
    assert compare_policy(x, policy="regression")["verdict"] == "invalid_eval"


def test_unknown_comparison_policy_is_rejected():
    from abevalflow.forge_compare import compare as compare_policy

    with pytest.raises(ValueError, match="policy"):
        compare_policy(rows(), policy="ignore-cost")


@pytest.mark.parametrize("policy,expected_exit", [("regression", 0), ("improvement", 1)])
def test_comparison_cli_exits_according_to_policy(tmp_path, monkeypatch, policy, expected_exit):
    import json

    from abevalflow.forge_compare import main

    x = rows()
    for row in x:
        row["baseline"].update(quality=1, critical_pass=True, output_tokens=90)
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps(x))
    monkeypatch.setattr(
        "sys.argv", ["compare", str(path), "--output", str(tmp_path / "result.json"), "--policy", policy]
    )
    assert main() == expected_exit
