"""Tests for OpenShellRunner in scripts/run_aeh.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from scripts.run_aeh import RUNNERS, OpenShellRunner, RunnerError, get_runner


class TestOpenShellRunnerRegistry:
    def test_openshell_runner_registered(self):
        assert "openshell" in RUNNERS
        assert RUNNERS["openshell"] is OpenShellRunner

    def test_get_runner_openshell(self):
        runner = get_runner("openshell", model="claude-sonnet")
        assert isinstance(runner, OpenShellRunner)
        assert runner.name == "openshell"
        assert runner.model == "claude-sonnet"


class TestOpenShellRunnerExecute:
    def test_requires_model(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_EVAL_OPENSHELL_IMAGE", "quay.io/example/openclaw:latest")
        monkeypatch.setenv("OPENSHELL_GATEWAY_ENDPOINT", "https://gw.example:17670")
        runner = OpenShellRunner()
        config = tmp_path / "eval.yaml"
        config.write_text(yaml.dump({"skill": "demo"}))
        with pytest.raises(RunnerError, match="--model"):
            runner.run_single(config, tmp_path / "reports" / "demo" / "run-1")

    def test_requires_sandbox_image(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AGENT_EVAL_OPENSHELL_IMAGE", raising=False)
        monkeypatch.setenv("OPENSHELL_GATEWAY_ENDPOINT", "https://gw.example:17670")
        runner = OpenShellRunner(model="claude-sonnet")
        config = tmp_path / "eval.yaml"
        config.write_text(yaml.dump({"skill": "demo"}))
        with pytest.raises(RunnerError, match="AGENT_EVAL_OPENSHELL_IMAGE"):
            runner.run_single(config, tmp_path / "reports" / "demo" / "run-1")

    def test_requires_gateway_endpoint(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_EVAL_OPENSHELL_IMAGE", "quay.io/example/openclaw:latest")
        monkeypatch.delenv("OPENSHELL_GATEWAY_ENDPOINT", raising=False)
        runner = OpenShellRunner(model="claude-sonnet")
        config = tmp_path / "eval.yaml"
        config.write_text(yaml.dump({"skill": "demo"}))
        with pytest.raises(RunnerError, match="OPENSHELL_GATEWAY_ENDPOINT"):
            runner.run_single(config, tmp_path / "reports" / "demo" / "run-1")

    @patch("subprocess.run")
    def test_execute_command_shape(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_EVAL_OPENSHELL_IMAGE", "quay.io/example/openclaw:latest")
        monkeypatch.setenv(
            "OPENSHELL_GATEWAY_ENDPOINT",
            "https://abeval-saw-gateway.forge-saw.svc.cluster.local:17670",
        )
        mock_run.return_value = MagicMock(returncode=0)

        reports = tmp_path / "reports"
        output = reports / "openclaw-forge" / "run-1"
        config = tmp_path / "eval.yaml"
        config.write_text(yaml.dump({"skill": "openclaw-forge", "runner": {"type": "openclaw"}}))

        runner = OpenShellRunner(model="claude-sonnet")
        assert runner.run_single(config, output, run_id="run-1") == 0

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        env = mock_run.call_args.kwargs.get("env") or mock_run.call_args[1].get("env")

        assert "-m" in cmd
        assert "agent_eval.openshell.run" in cmd
        assert "--config" in cmd
        assert str(config) in cmd
        assert "--model" in cmd
        assert "claude-sonnet" in cmd
        assert "--run-id" in cmd
        assert "run-1" in cmd
        assert "agent_eval.harbor.run" not in cmd
        assert env["AGENT_EVAL_RUNS_DIR"] == str(reports)
        assert Path(env["AGENT_EVAL_RUNS_DIR"]) == output.parent.parent

    @patch("subprocess.run")
    def test_honors_existing_agent_eval_runs_dir(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_EVAL_OPENSHELL_IMAGE", "quay.io/example/openclaw:0.0.1-1787755593")
        monkeypatch.setenv("OPENSHELL_GATEWAY_ENDPOINT", "http://openshell.example:8080")
        monkeypatch.setenv("AGENT_EVAL_RUNS_DIR", str(tmp_path / "custom-reports"))
        mock_run.return_value = MagicMock(returncode=0)

        output = tmp_path / "ignored" / "openclaw-forge" / "run-1"
        config = tmp_path / "eval.yaml"
        config.write_text(yaml.dump({"name": "forge-eval-rubrics"}))

        runner = OpenShellRunner(model="claude-sonnet")
        assert runner.run_single(config, output, run_id="run-1") == 0

        env = mock_run.call_args.kwargs.get("env") or mock_run.call_args[1].get("env")
        assert env["AGENT_EVAL_RUNS_DIR"] == str(tmp_path / "custom-reports")

    @patch("subprocess.run")
    def test_copies_results_when_eval_name_differs_from_output(
        self, mock_run, tmp_path, monkeypatch
    ):
        """nfz69: harness writes reports/<name>/<run-id>, wrapper passed submission-dir."""
        monkeypatch.setenv("AGENT_EVAL_OPENSHELL_IMAGE", "quay.io/example/openclaw:0.0.1-1787755593")
        monkeypatch.setenv("OPENSHELL_GATEWAY_ENDPOINT", "http://openshell.example:8080")

        reports = tmp_path / "reports"
        monkeypatch.setenv("AGENT_EVAL_RUNS_DIR", str(reports))
        output = reports / "openclaw-forge" / "aeh-openshell-forge-nsenter-nfz69"
        actual = reports / "forge-eval-rubrics" / "aeh-openshell-forge-nsenter-nfz69"
        config = tmp_path / "eval.yaml"
        config.write_text(
            yaml.dump(
                {
                    "name": "forge-eval-rubrics",
                    "execution": {"prompt": "{{ input.prompt }}"},
                }
            )
        )

        def fake_run(*args, **kwargs):
            actual.mkdir(parents=True, exist_ok=True)
            (actual / "summary.yaml").write_text("mean_reward: 1.0\n")
            (actual / "run_result.json").write_text("{}")
            return MagicMock(returncode=0)

        mock_run.side_effect = fake_run
        runner = OpenShellRunner(model="claude-sonnet")
        assert runner.run_single(config, output, run_id=output.name) == 0
        assert (output / "summary.yaml").is_file()
        assert (output / "run_result.json").is_file()

    @patch("subprocess.run")
    def test_patches_eval_yaml_judge_model(self, mock_run, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENT_EVAL_OPENSHELL_IMAGE", "quay.io/example/openclaw:0.0.1-1787755593")
        monkeypatch.setenv("OPENSHELL_GATEWAY_ENDPOINT", "http://openshell.example:8080")
        captured: dict = {}

        def fake_run(cmd, env=None, **kwargs):
            cfg = Path(cmd[cmd.index("--config") + 1])
            captured["path"] = cfg
            captured["yaml"] = yaml.safe_load(cfg.read_text())
            captured["env"] = env
            return MagicMock(returncode=0)

        mock_run.side_effect = fake_run
        output = tmp_path / "reports" / "openclaw-forge" / "run-1"
        config = tmp_path / "eval.yaml"
        config.write_text(
            yaml.dump(
                {
                    "name": "forge-eval-rubrics",
                    "models": {"skill": "claude-sonnet", "judge": "claude-sonnet-4-5@20250929"},
                    "dataset": {"path": "cases"},
                }
            )
        )
        runner = OpenShellRunner(model="claude-sonnet", judge_model="claude-sonnet")
        assert runner.run_single(config, output, run_id="run-1") == 0
        assert captured["path"] != config
        assert captured["yaml"]["models"]["judge"] == "claude-sonnet"
        assert captured["yaml"]["dataset"]["path"] == "cases"
        assert captured["env"]["EVAL_JUDGE_MODEL"] == "claude-sonnet"
        assert not captured["path"].exists()


class TestEvaluateOpenshellJudgeDeps:
    def test_openshell_evaluate_installs_anthropic(self):
        evaluate = Path(__file__).resolve().parents[1] / "pipeline" / "tasks" / "phases" / "evaluate.yaml"
        spec = yaml.safe_load(evaluate.read_text())["spec"]
        script = next(s["script"] for s in spec["steps"] if s["name"] == "aeh-openshell-eval")
        assert "anthropic" in script
        assert "aeh-judge-pkgs" in script
        assert "from scripts.aggregate_aeh import _extract_mean_reward" in script
        assert 'summary.get("mean_reward", 0.0)' not in script
        assert 'control-reward").write_text("0.0000")' not in script


class TestEvaluateOpenshellM365:
    def test_openshell_step_forwards_credentials_secret(self):
        evaluate = Path(__file__).resolve().parents[1] / "pipeline" / "tasks" / "phases" / "evaluate.yaml"
        spec = yaml.safe_load(evaluate.read_text())["spec"]
        assert any(p["name"] == "openshell-credentials-secret" for p in spec["params"])
        step = next(s for s in spec["steps"] if s["name"] == "aeh-openshell-eval")
        env_from = step.get("envFrom") or []
        assert env_from, "aeh-openshell-eval must envFrom openshell-credentials"
        secret_refs = [item.get("secretRef") or {} for item in env_from]
        assert any(
            ref.get("name") == "$(params.openshell-credentials-secret)"
            and ref.get("optional") is True
            for ref in secret_refs
        )
        env = {item["name"]: item for item in step["env"]}
        for key in (
            "M365_ACCESS_TOKEN",
            "M365_USER",
            "M365_TENANT_ID",
            "M365_CLIENT_ID",
            "M365_CLIENT_SECRET",
        ):
            ref = env[key]["valueFrom"]["secretKeyRef"]
            assert ref["name"] == "$(params.openshell-credentials-secret)"
            assert ref["key"] == key
            assert ref["optional"] is True
        assert "M365PRE" in step["script"]
        assert "M365 Graph credentials preflight" in step["script"]
        assert "oc create secret generic openshell-credentials" in step["script"]
        assert "M365_AUTH_HEADER_FILE and M365_GRAPH_CURL are created" in step["script"]


class TestEvaluateAehSingleParse:
    def test_harbor_aeh_eval_uses_extract_mean_reward(self):
        evaluate = Path(__file__).resolve().parents[1] / "pipeline" / "tasks" / "phases" / "evaluate.yaml"
        spec = yaml.safe_load(evaluate.read_text())["spec"]
        script = next(s["script"] for s in spec["steps"] if s["name"] == "aeh-eval")
        assert "from scripts.aggregate_aeh import _extract_mean_reward" in script
        assert 'summary.get("mean_reward", 0.0)' not in script
