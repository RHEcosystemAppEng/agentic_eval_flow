"""Tests for scripts/log_aeh_mlflow.py."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.log_aeh_mlflow import main


def _write_run(tmp_path: Path, *, skill: str = "demo-skill", run_id: str = "run-1") -> tuple[Path, Path]:
    runs = tmp_path / "reports"
    run_dir = runs / skill / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run_result.json").write_text(
        json.dumps(
            {
                "execution_mode": "harbor",
                "mean_reward": 0.8,
                "cost_usd": 0.12,
                "token_usage": {"input": 100, "output": 50},
                "model": "claude-sonnet",
            }
        )
    )
    (run_dir / "summary.yaml").write_text(yaml.safe_dump({"mean_reward": 0.8}))
    config = tmp_path / "eval.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "skill": skill,
                "mlflow": {"experiment": "aeh-demo"},
                "models": {"skill": "claude-sonnet"},
                "judges": [{"name": "ok", "check": "return True, 'ok'"}],
            }
        )
    )
    return runs, config


def test_skips_when_disabled(tmp_path: Path) -> None:
    runs, config = _write_run(tmp_path)
    rc = main(
        [
            "--run-id",
            "run-1",
            "--config",
            str(config),
            "--runs-dir",
            str(runs),
            "--tracking-uri",
            "http://mlflow.example:5000",
            "--enabled",
            "false",
        ]
    )
    assert rc == 0


def test_skips_when_uri_empty(tmp_path: Path) -> None:
    runs, config = _write_run(tmp_path)
    rc = main(
        [
            "--run-id",
            "run-1",
            "--config",
            str(config),
            "--runs-dir",
            str(runs),
            "--tracking-uri",
            "",
            "--enabled",
            "true",
        ]
    )
    assert rc == 0


def test_returns_error_when_mlflow_not_importable(tmp_path: Path, monkeypatch) -> None:
    runs, config = _write_run(tmp_path)
    import builtins

    real_import = builtins.__import__

    def _block_mlflow(name, *args, **kwargs):
        if name == "mlflow" or name.startswith("mlflow."):
            raise ImportError("blocked")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_mlflow)
    rc = main(
        [
            "--run-id",
            "run-1",
            "--config",
            str(config),
            "--runs-dir",
            str(runs),
            "--tracking-uri",
            "http://mlflow.example:5000",
            "--enabled",
            "true",
            "--actions",
            "log-results",
        ]
    )
    assert rc == 1


def test_minimal_logger_with_mock_mlflow(tmp_path: Path, monkeypatch) -> None:
    runs, config = _write_run(tmp_path)

    class _FakeRun:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    calls: dict[str, list] = {"params": [], "metrics": [], "artifacts": []}

    class _FakeMlflow:
        def set_tracking_uri(self, uri):
            calls["uri"] = uri

        def set_experiment(self, name):
            calls["experiment"] = name

        def start_run(self, run_name=None):
            calls["run_name"] = run_name
            return _FakeRun()

        def log_param(self, k, v):
            calls["params"].append((k, v))

        def log_metric(self, k, v):
            calls["metrics"].append((k, v))

        def log_artifact(self, path):
            calls["artifacts"].append(path)

    import sys

    monkeypatch.setitem(sys.modules, "mlflow", _FakeMlflow())

    # Restrict to log-results so missing AEH push/sync scripts do not matter.
    monkeypatch.setattr(
        "scripts.log_aeh_mlflow._resolve_log_results_script",
        lambda: None,
    )
    rc = main(
        [
            "--run-id",
            "run-1",
            "--config",
            str(config),
            "--runs-dir",
            str(runs),
            "--tracking-uri",
            "http://mlflow.example:5000",
            "--enabled",
            "true",
            "--actions",
            "log-results",
        ]
    )
    assert rc == 0
    assert calls["uri"] == "http://mlflow.example:5000"
    assert calls["experiment"] == "aeh-demo"
    assert ("mean_reward", 0.8) in calls["metrics"]
    assert ("tokens_input", 100.0) in calls["metrics"]


def test_minimal_logger_derives_mean_reward_from_likert_summary(tmp_path: Path, monkeypatch) -> None:
    """OpenShell summary.yaml often has no top-level mean_reward; derive from judges."""
    runs = tmp_path / "reports"
    run_dir = runs / "forge-eval-rubrics" / "aeh-openshell-openclaw-x"
    run_dir.mkdir(parents=True)
    (run_dir / "run_result.json").write_text('{"execution_mode": "openshell"}')
    (run_dir / "summary.yaml").write_text(
        yaml.safe_dump(
            {
                "judges": {
                    "analysis_accuracy": {"mean": 2.0, "score_range": [1, 5]},
                },
                "per_case": {
                    "analysis-panel": {"analysis_accuracy": {"value": 2}},
                },
            }
        )
    )
    config = tmp_path / "eval.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "name": "forge-eval-rubrics",
                "mlflow": {"experiment": "forge-eval-rubrics"},
                "judges": [{"name": "analysis_accuracy", "llm_rubric": "x"}],
            }
        )
    )
    calls: dict = {"metrics": []}

    class _FakeRun:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class _FakeMlflow:
        def set_tracking_uri(self, uri):
            pass

        def set_experiment(self, name):
            pass

        def start_run(self, run_name=None):
            return _FakeRun()

        def log_param(self, k, v):
            pass

        def log_metric(self, k, v):
            calls["metrics"].append((k, v))

        def log_artifact(self, path):
            pass

    import sys

    monkeypatch.setitem(sys.modules, "mlflow", _FakeMlflow())
    monkeypatch.setattr("scripts.log_aeh_mlflow._resolve_log_results_script", lambda: None)
    rc = main(
        [
            "--run-id",
            "aeh-openshell-openclaw-x",
            "--config",
            str(config),
            "--runs-dir",
            str(runs),
            "--tracking-uri",
            "http://mlflow.example:5000",
            "--experiment",
            "aeh-openshell-openclaw-x",
            "--enabled",
            "true",
            "--actions",
            "log-results",
        ]
    )
    assert rc == 0
    mean = dict(calls["metrics"]).get("mean_reward")
    assert mean is not None
    assert mean != 0.0


def test_minimal_logger_uses_pipeline_experiment_override(tmp_path: Path, monkeypatch) -> None:
    """--experiment forces one experiment per PipelineRun (ignores eval.yaml mlflow.experiment)."""
    runs, config = _write_run(tmp_path)
    calls: dict[str, list] = {"params": []}

    class _FakeRun:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class _FakeMlflow:
        def set_tracking_uri(self, uri):
            pass

        def set_experiment(self, name):
            calls["experiment"] = name

        def start_run(self, run_name=None):
            calls["run_name"] = run_name
            return _FakeRun()

        def log_param(self, k, v):
            calls["params"].append((k, v))

        def log_metric(self, k, v):
            pass

        def log_artifact(self, path):
            pass

    import sys

    monkeypatch.setitem(sys.modules, "mlflow", _FakeMlflow())
    monkeypatch.setattr("scripts.log_aeh_mlflow._resolve_log_results_script", lambda: None)
    rc = main(
        [
            "--run-id",
            "run-1",
            "--config",
            str(config),
            "--runs-dir",
            str(runs),
            "--tracking-uri",
            "http://mlflow.example:5000",
            "--experiment",
            "aeh-mlflow-single-abc123",
            "--enabled",
            "true",
            "--actions",
            "log-results",
        ]
    )
    assert rc == 0
    assert calls["experiment"] == "aeh-mlflow-single-abc123"
    assert calls["run_name"] == "run-1"
    assert ("pipeline_experiment", "aeh-mlflow-single-abc123") in calls["params"]


def test_resolve_run_dir_discovers_when_config_skill_mismatches(tmp_path: Path) -> None:
    """Pairwise control yaml skill often differs from the shared reports folder."""
    from scripts.log_aeh_mlflow import _reports_skill_name, _resolve_run_dir, _write_patched_config

    runs = tmp_path / "reports"
    run_id = "control-pr-1"
    real_skill = "aeh-hello-world-pairwise"
    (runs / real_skill / run_id).mkdir(parents=True)
    config_skill = "aeh-hello-world-pairwise-control"
    found = _resolve_run_dir(runs, config_skill, run_id)
    assert found == runs / real_skill / run_id
    assert _reports_skill_name(runs, config_skill, run_id) == real_skill

    cfg = tmp_path / "eval-control.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "skill": config_skill,
                "mlflow": {"experiment": "aeh-hello-world-pairwise-control"},
            }
        )
    )
    patched = _write_patched_config(cfg, experiment="pr-1", reports_skill=real_skill)
    try:
        data = yaml.safe_load(patched.read_text())
        assert data["skill"] == real_skill
        assert data["mlflow"]["experiment"] == "pr-1"
    finally:
        patched.unlink(missing_ok=True)


def test_variant_from_run_id() -> None:
    from scripts.log_aeh_mlflow import _variant_from_run_id

    assert _variant_from_run_id("control-foo") == "control"
    assert _variant_from_run_id("treatment-foo") == "treatment"
    assert _variant_from_run_id("foo") is None


def test_default_schema_mapping_from_input_yaml(tmp_path: Path) -> None:
    from scripts.log_aeh_mlflow import _default_schema_mapping

    config = tmp_path / "eval.yaml"
    cases = tmp_path / "cases" / "case-001"
    cases.mkdir(parents=True)
    (cases / "input.yaml").write_text(yaml.safe_dump({"prompt": "hello"}))
    (cases / "reference.md").write_text("gold\n")
    config.write_text(yaml.safe_dump({"skill": "demo", "dataset": {"path": "cases"}}))
    mapping = _default_schema_mapping(config)
    assert mapping is not None
    assert mapping["inputs"]["prompt"] == "input.yaml:prompt"
    assert mapping["expectations"]["reference"] == "reference.md:__file__"


def test_upstream_noop_missing_mlflow_markers() -> None:
    from scripts.log_aeh_mlflow import _upstream_noop_missing_mlflow

    assert _upstream_noop_missing_mlflow("MLflow not installed\n") is True
    assert _upstream_noop_missing_mlflow("No module named 'mlflow'\n") is True
    assert _upstream_noop_missing_mlflow("Logged 3 runs\n") is False


def test_run_aeh_log_results_detects_missing_mlflow_noop(tmp_path: Path, monkeypatch) -> None:
    from scripts import log_aeh_mlflow as mod

    script = tmp_path / "log_results.py"
    script.write_text("# stub\n")

    class _Result:
        returncode = 0
        stdout = "MLflow not installed — skipping\n"
        stderr = ""

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Result())
    rc = mod._run_aeh_log_results(
        script=script,
        run_id="run-1",
        config=tmp_path / "eval.yaml",
        runs_dir=tmp_path / "reports",
    )
    assert rc == 2


def test_resolve_run_dir_prefers_exact_then_pairwise_prefixes(tmp_path: Path) -> None:
    from scripts.log_aeh_mlflow import _resolve_run_dir

    skill = "demo-skill"
    runs = tmp_path / "reports"
    exact = runs / skill / "run-1"
    exact.mkdir(parents=True)
    assert _resolve_run_dir(runs, skill, "run-1") == exact

    bare_id = "pipe-abc"
    treatment = runs / skill / f"treatment-{bare_id}"
    treatment.mkdir(parents=True)
    assert _resolve_run_dir(runs, skill, bare_id) == treatment

    control_only = "pipe-ctrl"
    control = runs / skill / f"control-{control_only}"
    control.mkdir(parents=True)
    assert _resolve_run_dir(runs, skill, control_only) == control

    try:
        _resolve_run_dir(runs, skill, "missing-run")
        raise AssertionError("expected FileNotFoundError")
    except FileNotFoundError as exc:
        assert "missing-run" in str(exc)


def test_prompt_mode_mlflow_patch_does_not_set_skill() -> None:
    from scripts.log_aeh_mlflow import _apply_mlflow_eval_patch, _config_reports_leaf

    raw = {
        "name": "forge-eval-rubrics",
        "execution": {"prompt": "{{ input.prompt }}"},
        "mlflow": {"experiment": "forge-eval-rubrics"},
    }
    assert _config_reports_leaf(raw, "openclaw-forge") == "forge-eval-rubrics"
    patched = _apply_mlflow_eval_patch(
        raw,
        experiment="aeh-openshell-openclaw-abc",
        reports_skill="forge-eval-rubrics",
        tracking_uri="http://abevalflow-mlflow.ab-eval-flow.svc.cluster.local:5000",
    )
    assert "skill" not in patched
    assert patched["name"] == "forge-eval-rubrics"
    assert patched["execution"]["prompt"] == "{{ input.prompt }}"
    assert patched["mlflow"]["experiment"] == "aeh-openshell-openclaw-abc"
    assert patched["mlflow"]["tracking_uri"].startswith("http://abevalflow-mlflow")


def test_skill_mode_mlflow_patch_still_sets_skill() -> None:
    from scripts.log_aeh_mlflow import _apply_mlflow_eval_patch

    patched = _apply_mlflow_eval_patch(
        {"skill": "aeh-hello-world", "name": "aeh-hello-world"},
        experiment="pr-1",
        reports_skill="aeh-hello-world",
        tracking_uri="http://mlflow.example:5000",
    )
    assert patched["skill"] == "aeh-hello-world"
    assert patched["mlflow"]["experiment"] == "pr-1"
