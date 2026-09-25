import json

import pytest

from abevalflow.forge_compare import build_pair
from scripts.compare_forge_experiment import verify_infrastructure


def test_runner_propagates_comparison_failure_after_pre_agent_failure(tmp_path, monkeypatch):
    from subprocess import CompletedProcess

    from scripts import run_forge_experiment as runner

    plan = tmp_path / "experiment.json"
    plan.write_text(
        json.dumps(
            {
                "arms": {arm: {"directory": arm} for arm in ("baseline", "candidate")},
                "order": [{"pair": 0, "arm": "baseline"}],
            }
        )
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "run",
            "--experiment",
            str(plan),
            "--namespace",
            "test",
            "--base-run",
            str(tmp_path / "base.json"),
            "--harness",
            str(tmp_path),
        ],
    )
    results = iter(
        [
            {"metadata": {"name": "failed-run"}},
            {"status": {"conditions": [{"type": "Succeeded", "status": "False", "reason": "Failed"}]}},
            {"items": []},
        ]
    )
    monkeypatch.setattr(runner.subprocess, "check_output", lambda *a, **kw: json.dumps(next(results)).encode())
    calls = []

    def execute(args, **kwargs):
        calls.append(args)
        return CompletedProcess(
            args, 1 if any("compare_forge_experiment.py" in str(a) for a in args) else 0, stdout="", stderr=""
        )

    monkeypatch.setattr(runner.subprocess, "run", execute)
    assert runner.main() == 1
    assert "compare_forge_experiment.py" in calls[-1][1]


def test_prepare_freezes_comparison_policy(tmp_path, monkeypatch):
    import base64

    from scripts import prepare_forge_experiment as prepare

    source = tmp_path / "submission"
    (source / "cases/new").mkdir(parents=True)
    (source / "cases/new/input.yaml").write_text("prompt: draft")
    (source / "eval.yaml").write_text("dataset: {path: cases}\nrunner: {settings: {}}\n")
    output = tmp_path / "experiment"
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare",
            "--submission",
            str(source),
            "--output",
            str(output),
            "--baseline",
            "a" * 40,
            "--candidate",
            "b" * 40,
            "--case",
            "new",
            "--policy",
            "regression",
        ],
    )
    monkeypatch.setattr(
        prepare.subprocess,
        "check_output",
        lambda *a, **kw: json.dumps({"content": base64.b64encode(b"skill").decode()}).encode(),
    )
    prepare.main()
    assert json.loads((output / "experiment.json").read_text())["policy"] == "regression"


def test_pair_rejects_changed_loaded_identity(tmp_path):
    for arm in ("baseline", "candidate"):
        root = tmp_path / arm
        root.mkdir()
        (root / "run_result.json").write_text(
            json.dumps({"evaluation_status": "passed", "usage": {"complete": True, "output": 10}})
        )
        (root / "provenance.json").write_text(
            json.dumps(
                {
                    "image": "digest",
                    "model": "model",
                    "effort": "high",
                    "prompt_sha256": "p",
                    "harness_files": {"runtime": "hash"},
                    "model_config_sha256": "m",
                    "initial_files": {},
                    "loaded_files": {"USER.md": arm, "skills/forge-drafts/SKILL.md": arm},
                }
            )
        )
    with pytest.raises(ValueError, match="more than the intended skill"):
        build_pair(
            tmp_path / "baseline",
            tmp_path / "candidate",
            case="test",
            pair=0,
            skill_path="skills/forge-drafts/SKILL.md",
        )


def test_quality_failure_allowed_but_storage_failure_is_not(tmp_path):
    record = {
        "run": "run",
        "directory": str(tmp_path),
        "status": "Failed",
        "mlflow_verified": True,
        "stages": {k: "True" for k in ("prepare", "evaluate", "analyze", "store")},
    }
    with pytest.raises(OSError):
        verify_infrastructure(record)
    (tmp_path / "storage-attestation.json").write_text(
        json.dumps({"run": "run", "status": "verified", "objects": [{"key": "artifact"}]})
    )
    verify_infrastructure(record)
    record["stages"]["store"] = "False"
    with pytest.raises(ValueError, match="infrastructure"):
        verify_infrastructure(record)
