"""Regression boundaries for native Harbor managed mocks and paired evidence."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from abevalflow.harbor_extensions.harbor_evidence import validate_attempt
from abevalflow.harbor_extensions.mcp_connection import configure_job
from scripts.run_harbor_mcp import prepare_task

IMAGE = "registry.invalid/image@sha256:" + "a" * 64


def evidence():
    pods = [{"name": "mock", "uid": "mock-uid"}, {"name": "client", "uid": "client-uid"}]
    data = {
        "attempt-inventory.json": {
            "uid": "uid",
            "variant": "control",
            "mock": "mock",
            "client": "client",
            "client_image": IMAGE,
            "pods": pods,
        },
        "target.json": {
            "uid": "uid",
            "name": "mock",
            "mock": "fixture",
            "endpoint": "http://mock.ns.svc:8000/mcp",
            "descriptor": {"image": IMAGE},
        },
        "readiness.json": {
            "uid": "uid",
            "name": "mock",
            "endpoint": "http://mock.ns.svc:8000/mcp",
            "ready": True,
            "tools": ["check_connection"],
        },
        "behavior.json": {"passed": True, "calls": [{"tool": "check_connection", "outcome": "ok"}]},
        "answer-judge.json": {"passed": True},
        "trajectory.json": {"steps": [1]},
        "trial/result.json": {"verifier_result": {"rewards": {"reward": 1}}, "exception_info": None},
        "cleanup.json": {"uid": "uid", "variant": "control", "deleted": True, "pods": pods},
    }
    for pod in pods:
        data[f"pod-status/{pod['name']}.json"] = {
            "name": pod["name"],
            "pod_uid": pod["uid"],
            "labels": {"abevalflow.io/mcp-run": "uid"},
            "images": [IMAGE],
            "status": {"phase": "Failed", "container_statuses": [{"image_id": IMAGE}]},
        }
    encoded = {"root/" + k: json.dumps(v).encode() for k, v in data.items()}
    encoded["root/pod-logs/mock.log"] = (
        b'2026-11-16T12:00:00Z {"event":"tool_call","tool":"check_connection","outcome":"ok"}\n'
    )
    encoded["root/pod-logs/client.log"] = b"child finished"
    return encoded


def test_complete_native_evidence():
    assert validate_attempt(evidence(), "uid", "control", "mock")["client"] == "client"


@pytest.mark.parametrize(
    "path,field,value",
    [
        ("attempt-inventory.json", "pods", []),
        ("readiness.json", "name", "previous-mock"),
        ("target.json", "uid", "stale"),
        ("pod-status/mock.json", "pod_uid", "wrong"),
        ("pod-status/client.json", "images", ["image:latest"]),
        ("behavior.json", "calls", []),
        ("answer-judge.json", "passed", False),
        ("cleanup.json", "deleted", False),
    ],
)
def test_reject_incomplete_or_inconsistent_evidence(path, field, value):
    objects = evidence()
    item = json.loads(objects["root/" + path])
    item[field] = value
    objects["root/" + path] = json.dumps(item).encode()
    with pytest.raises(RuntimeError):
        validate_attempt(objects, "uid", "control", "mock")


def test_empty_required_documents_rejected():
    objects = evidence()
    for key in objects:
        if key.endswith(".json"):
            objects[key] = b"{}"
    with pytest.raises(RuntimeError):
        validate_attempt(objects, "uid", "control", "mock")


def test_stage_same_docs_only_remove_skill(tmp_path):
    source = tmp_path / "source"
    skill = source / "environment/skills/create/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("our skill")
    docs = source / "environment/references"
    docs.mkdir()
    (docs / "MCP.md").write_text("real guide")
    control = prepare_task(source, tmp_path / "control", "control")
    treatment = prepare_task(source, tmp_path / "treatment", "treatment")
    assert not (control / "environment/skills").exists()
    assert (treatment / "environment/skills/create/SKILL.md").read_text() == "our skill"
    assert (control / "environment/references/MCP.md").read_bytes() == (
        treatment / "environment/references/MCP.md"
    ).read_bytes()
    (source / "escape").symlink_to("/tmp")
    with pytest.raises(ValueError, match="symlink"):
        prepare_task(source, tmp_path / "unsafe", "control")


def test_configure_native_job_preserves_task_and_rejects_stale(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    (task / "task.toml").write_text('version="1.0"\n[agent]\ntimeout_sec=600\n')
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"uid": "uid", "name": "mock", "endpoint": "http://mock.ns.svc:8000/mcp"}))
    config = {"tasks": [{"path": str(task)}]}
    result = configure_job(deepcopy(config), task, target, "uid", "run")
    assert result["environment"]["kwargs"]["mcp_run_uid"] == "uid"
    assert "timeout_sec=600" in (task / "task.toml").read_text()
    with pytest.raises(ValueError, match="stale"):
        configure_job(config, task, target, "other", "run")
    with pytest.raises(ValueError, match="one case"):
        configure_job({**config, "n_attempts": 2}, task, target, "uid", "run")


def test_harbor_profile_separates_controller_and_child():
    import yaml

    root = Path(__file__).resolve().parents[1]
    pipeline = yaml.safe_load((root / "pipeline/pipelines/ci-pipeline-harbor-mcp.yaml").read_text())
    tasks = {t["name"]: t for t in pipeline["spec"]["tasks"]}
    assert set(tasks) == {"prepare", "test", "start-mcp", "evaluate-harbor", "store"}
    assert tasks["evaluate-harbor"]["taskRef"]["name"] == "evaluate-harbor-mcp"
    assert "evaluate-harbor.results" in str(pipeline["spec"]["results"])
    run = yaml.safe_load((root / "pipeline/runs/harbor-mcp-pipelinerun.yaml").read_text())
    overrides = {s["pipelineTaskName"]: s for s in run["spec"]["taskRunSpecs"]}
    assert overrides["evaluate-harbor"]["serviceAccountName"] == "harbor-mcp-lifecycle"


def test_catalog_is_immutable_complete_and_content_addressed(tmp_path):
    import yaml

    from scripts.render_mcp_catalog import render

    for name in ["first", "second"]:
        (tmp_path / (name + ".yaml")).write_text(yaml.safe_dump({"image": IMAGE}))
    original = render(tmp_path)
    assert original["immutable"] is True
    assert set(original["data"]) == {"first.yaml", "second.yaml"}
    assert render(tmp_path) == original
    (tmp_path / "second.yaml").write_text(yaml.safe_dump({"image": IMAGE, "port": 9000}))
    assert render(tmp_path)["metadata"]["name"] != original["metadata"]["name"]


def test_debug_retention_keeps_archival_but_gates_workspace_release():
    import yaml

    root = Path(__file__).resolve().parents[1]
    pipeline = yaml.safe_load((root / "pipeline/pipelines/ci-pipeline-harbor-mcp.yaml").read_text())["spec"]
    params = {p["name"]: p for p in pipeline["params"]}
    assert params["retain-debug-workspace"]["default"] == "true"
    finalizer = pipeline["finally"][0]
    assert {p["name"]: p["value"] for p in finalizer["params"]}["retain-debug-workspace"] == (
        "$(params.retain-debug-workspace)"
    )
    task = yaml.safe_load((root / "pipeline/tasks/post/cleanup_harbor_mcp.yaml").read_text())["spec"]
    steps = {s["name"]: s for s in task["steps"]}
    assert "when" not in steps["archive-evidence"]
    assert steps["release-workspace"]["when"][0]["values"] == ["false"]
    assert steps["report-retained"]["when"][0]["values"] == ["true"]
    assert "retained" in str(steps["report-retained"]["args"])


def test_console_stream_preserves_redacted_output_and_failure_status(tmp_path, monkeypatch, capsys):
    import sys

    from scripts.run_harbor_mcp import run_logged

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-only-stream-secret")
    monkeypatch.setenv("MINIO_ACCESS_KEY", "test-only-storage-key")
    log = tmp_path / "harbor-process.log"
    command = [
        sys.executable,
        "-u",
        "-c",
        "import os, sys; print(os.environ['ANTHROPIC_API_KEY']); "
        "print(os.environ['MINIO_ACCESS_KEY'], file=sys.stderr); sys.exit(7)",
    ]
    assert run_logged(command, log) == 7
    assert log.read_text() == capsys.readouterr().out == "[REDACTED]\n[REDACTED]\n"


def test_evidence_redaction_preserves_structural_storage_username_overlap(tmp_path, monkeypatch):
    from scripts.run_harbor_mcp import redact_tree

    monkeypatch.setenv("MINIO_ACCESS_KEY", "abevalflow")
    monkeypatch.setenv("MINIO_SECRET_KEY", "test-only-private-storage-password")
    snapshot = tmp_path / "producer.json"
    snapshot.write_text(
        json.dumps(
            {
                "labels": {"abevalflow.io/mcp-run": "run-uid"},
                "error": "test-only-private-storage-password",
            }
        )
    )
    redact_tree(tmp_path)
    saved = json.loads(snapshot.read_text())
    assert saved["labels"] == {"abevalflow.io/mcp-run": "run-uid"}
    assert saved["error"] == "[REDACTED]"
