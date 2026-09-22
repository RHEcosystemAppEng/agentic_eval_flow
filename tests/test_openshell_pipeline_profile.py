"""Tests for the OpenShell OpenClaw pipeline profile."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "pipeline" / "pipelines" / "ci-pipeline-openshell.yaml"
CI = REPO / "pipeline" / "pipelines" / "ci-pipeline.yaml"
CI_DEV = REPO / "pipeline" / "pipelines" / "ci-pipeline-dev.yaml"
SUBMISSION = REPO / "submissions" / "openclaw-forge"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _param_defaults(spec: dict) -> dict[str, str | None]:
    return {p["name"]: p.get("default") for p in spec["params"]}


def _task_names(spec: dict) -> list[str]:
    return [t["name"] for t in spec["tasks"]]


class TestOpenshellPipelineProfile:
    def test_submission_uses_only_image_owned_chief_of_staff_assets(self):
        config = _load(SUBMISSION / "eval.yaml")
        assert config["dataset"]["workspace"]["files"] == []
        prompt = config["runner"]["system_prompt"]
        assert "immutable Forge SAW image owns" in prompt
        assert not (SUBMISSION / "CLAW.md").exists()
        assert not any(p.is_file() for p in (SUBMISSION / "workspace").rglob("*"))
        assert not any(p.is_file() for p in (SUBMISSION / "adapters").rglob("*"))

    def test_named_pipeline_omits_test_and_red_team(self):
        spec = _load(PIPELINE)["spec"]
        names = _task_names(spec)
        assert names == ["prepare", "evaluate", "analyze", "store"]
        assert "test" not in names
        assert "red-team" not in names
        evaluate = next(t for t in spec["tasks"] if t["name"] == "evaluate")
        assert evaluate["runAfter"] == ["prepare"]

    def test_openshell_defaults(self):
        defaults = _param_defaults(_load(PIPELINE)["spec"])
        assert defaults["eval-engine"] == "aeh_openshell_openclaw"
        assert defaults["submission-dir"] == "openclaw-forge"
        assert defaults["enable-ai-generation"] == "false"
        assert defaults["aeh-runner"] == "openshell"
        assert defaults["aeh-openshell-image"] == "registry.access.redhat.com/ubi9/python-311:9.6"
        assert defaults["openshell-sandbox-image"] == (
            "ghcr.io/rh-forge/openclaw-saw-agent@sha256:bcc55e9b7a36d5f65e8ffc75962496f8b3617762a4cdb37fd1cf54611b72d41a"
        )
        assert defaults["enable-mlflow"] == "true"
        assert defaults["mlflow-tracking-uri"] == (
            "http://abevalflow-mlflow.gz-forge-eval.svc.cluster.local:5000"
        )
        analyze = next(t for t in _load(PIPELINE)["spec"]["tasks"] if t["name"] == "analyze")
        scan = next(p for p in analyze["params"] if p["name"] == "security-scan-mode")
        assert scan["value"] == "disabled"

    def test_evaluate_openshell_step_logs_mlflow(self):
        evaluate = (REPO / "pipeline" / "tasks" / "phases" / "evaluate.yaml").read_text()
        openshell = evaluate.split("name: aeh-openshell-eval", 1)[1].split("name: aeh-pairwise", 1)[0]
        assert "log_aeh_mlflow.py" in openshell
        assert "=== AEH OpenShell MLflow logging ===" in openshell
        assert "Skipping AEH OpenShell MLflow" in openshell
        # AEH harness sees the tracking URI during the run; CI logger runs after.
        uri_note = "MLFLOW_TRACKING_URI set for AEH harness + CI logger"
        assert uri_note in openshell
        assert openshell.find(uri_note) < openshell.find("scripts/run_aeh.py")

    def test_harbor_profiles_still_include_test(self):
        for path in (CI, CI_DEV):
            names = _task_names(_load(path)["spec"])
            assert "test" in names
            assert names[0] == "prepare"
