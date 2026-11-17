"""Regression boundaries for native Harbor managed mocks and paired evidence."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from abevalflow.harbor_extensions.harbor_evidence import validate_attempt, validate_judge
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
    (task / "instruction.md").write_text("Use the named skill.\nActual task request.")
    (task / "environment").mkdir()
    (task / "environment" / "CLAUDE.md").write_text("The shared context, verbatim.")
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"uid": "uid", "name": "mock", "endpoint": "http://mock.ns.svc:8000/mcp"}))
    config = {"tasks": [{"path": str(task)}]}
    result = configure_job(deepcopy(config), task, target, "uid", "run")
    assert result["environment"]["kwargs"]["mcp_run_uid"] == "uid"
    assert "timeout_sec=600" in (task / "task.toml").read_text()
    assert json.loads((task / "tests" / "judge-context.json").read_text()) == {
        "task_request": "Use the named skill.\nActual task request.",
        "shared_environment_context": "The shared context, verbatim.",
    }
    assert not (task / "environment" / "judge-context.json").exists()
    (task / "environment" / "CLAUDE.md").unlink()
    configure_job(deepcopy(config), task, target, "uid", "run")
    assert json.loads((task / "tests" / "judge-context.json").read_text())["shared_environment_context"] == ""
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


def numeric_evidence():
    objects = evidence()
    behavior = json.loads(objects["root/behavior.json"])
    behavior.update(
        scoring_version=2,
        task_completion={"checks": {"created": True, "read": False}, "score": 0.5, "passed": False},
        protocol_compliance={"checks": {"connection": True}, "score": 1.0, "passed": True},
        score=0.75,
        passed=False,
        failures=[{"check": "read"}],
    )
    judge = {
        "scoring_version": 2,
        "score": 0.7,
        "passed": False,
        "reason": "Partially supported reasoning",
        "findings": [{"issue": "unsupported claim", "evidence": "observed mismatch", "deduction": 0.3}],
    }
    breakdown = {
        "scoring_version": 2,
        "weights": dict.fromkeys(("task_completion", "protocol_compliance", "llm_judgment"), 1 / 3),
        "task_completion": behavior["task_completion"],
        "protocol_compliance": behavior["protocol_compliance"],
        "llm_judgment": judge,
        "failures": behavior["failures"],
        "overall_score": (0.5 + 1 + 0.7) / 3,
        "overall_passed": False,
    }
    for name, value in (("behavior", behavior), ("answer-judge", judge), ("evaluation-breakdown", breakdown)):
        objects[f"root/{name}.json"] = json.dumps(value).encode()
    objects["root/trial/result.json"] = json.dumps(
        {"verifier_result": {"rewards": {"reward": breakdown["overall_score"]}}, "exception_info": None}
    ).encode()
    final = {"type": "result", "result": "Actual final answer", "is_error": False}
    raw = json.dumps({"type": "system", "subtype": "init"}) + "\n" + json.dumps(final) + "\n"
    objects["root/agent/claude-code.txt"] = raw.encode()
    supplied = {
        "task_context": {"task_request": "Actual task", "shared_environment_context": "Shared instructions"},
        "full_native_trace": raw,
        "final_result": final,
        "native_trace_format": "claude-code",
    }
    objects["root/llm-judge-input.json"] = json.dumps(
        {
            "model": "claude-sonnet",
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "Semantic rubric"},
                {"role": "user", "content": json.dumps(supplied)},
            ],
        }
    ).encode()
    return objects


def change_evidence(objects, path, keys, value):
    item = json.loads(objects["root/" + path])
    cursor = item
    for key in keys[:-1]:
        cursor = cursor[key]
    cursor[keys[-1]] = value
    objects["root/" + path] = json.dumps(item).encode()


def test_numeric_evidence_retains_partial_credit_and_legacy_binary_compatibility():
    assert validate_attempt(numeric_evidence(), "uid", "control", "mock")["client"] == "client"
    objects = evidence()
    change_evidence(objects, "behavior.json", ["passed"], False)
    change_evidence(objects, "trial/result.json", ["verifier_result", "rewards", "reward"], 0)
    assert validate_attempt(objects, "uid", "control", "mock")["client"] == "client"


@pytest.mark.parametrize("value", [True, "0.7", None, float("nan"), float("inf"), -0.1, 1.1, 10**400])
def test_numeric_judge_rejects_invalid_scores(value):
    judge = json.loads(numeric_evidence()["root/answer-judge.json"])
    judge["score"] = value
    with pytest.raises(RuntimeError):
        validate_judge(judge)


@pytest.mark.parametrize(
    "path,keys,value",
    [
        ("behavior.json", ["task_completion", "score"], 0.9),
        ("behavior.json", ["task_completion", "checks"], {}),
        ("behavior.json", ["task_completion", "checks", "created"], 1),
        ("behavior.json", ["task_completion", "passed"], True),
        ("behavior.json", ["score"], 0.9),
        ("behavior.json", ["passed"], True),
        ("answer-judge.json", ["passed"], True),
        ("answer-judge.json", ["reason"], None),
        ("answer-judge.json", ["reason"], "  "),
        ("answer-judge.json", ["findings"], [{"issue": "", "evidence": "y", "deduction": 0.3}]),
        ("answer-judge.json", ["findings"], [{"issue": "x", "evidence": "  ", "deduction": 0.3}]),
        ("answer-judge.json", ["findings"], []),
        ("answer-judge.json", ["findings"], [{"issue": "x", "evidence": "y", "deduction": 0}]),
        ("evaluation-breakdown.json", ["overall_score"], 0.8),
        ("evaluation-breakdown.json", ["overall_passed"], True),
        ("evaluation-breakdown.json", ["weights", "task_completion"], 0.5),
        ("evaluation-breakdown.json", ["llm_judgment", "score"], 0.8),
        ("evaluation-breakdown.json", ["llm_judgment", "scoring_version"], 2.0),
        ("evaluation-breakdown.json", ["task_completion", "checks", "read"], True),
        ("evaluation-breakdown.json", ["failures"], []),
        ("trial/result.json", ["verifier_result", "rewards", "reward"], 1.0),
    ],
)
def test_numeric_evidence_rejects_inconsistent_assessments(path, keys, value):
    objects = numeric_evidence()
    change_evidence(objects, path, keys, value)
    with pytest.raises(RuntimeError):
        validate_attempt(objects, "uid", "control", "mock")


@pytest.mark.parametrize("version", [1, 3, True, "2", None])
def test_unknown_explicit_scoring_versions_fail(version):
    objects = numeric_evidence()
    change_evidence(objects, "behavior.json", ["scoring_version"], version)
    with pytest.raises(RuntimeError, match="version"):
        validate_attempt(objects, "uid", "control", "mock")


@pytest.mark.parametrize("remove_all", [False, True])
def test_scored_fields_cannot_downgrade_to_legacy(remove_all):
    objects = numeric_evidence()
    for name in (
        ["behavior.json", "answer-judge.json", "evaluation-breakdown.json"] if remove_all else ["behavior.json"]
    ):
        item = json.loads(objects["root/" + name])
        item.pop("scoring_version")
        objects["root/" + name] = json.dumps(item).encode()
    with pytest.raises(RuntimeError):
        validate_attempt(objects, "uid", "control", "mock")


def test_numeric_evidence_requires_breakdown_and_semantic_findings():
    objects = numeric_evidence()
    del objects["root/evaluation-breakdown.json"]
    with pytest.raises(RuntimeError):
        validate_attempt(objects, "uid", "control", "mock")
    judge = {"scoring_version": 2, "score": 1, "passed": True, "reason": "All supported", "findings": []}
    assert validate_judge(judge) == 1
    judge.update(score=0.8, passed=False, findings=[{"issue": "x", "evidence": "y", "deduction": 0.2}])
    assert validate_judge(judge) == 0.8
    judge["findings"][0]["deduction"] = 0.1
    with pytest.raises(RuntimeError, match="deductions"):
        validate_judge(judge)
    judge["findings"] = [
        {"issue": "x", "evidence": "y", "deduction": 0.6},
        {"issue": "z", "evidence": "w", "deduction": 0.5},
    ]
    with pytest.raises(RuntimeError, match="deductions"):
        validate_judge(judge)


@pytest.mark.parametrize("perfect", [False, True])
def test_versioned_minimum_and_maximum_score_are_valid(perfect):
    objects = numeric_evidence()
    behavior = json.loads(objects["root/behavior.json"])
    for key in ("task_completion", "protocol_compliance"):
        category = behavior[key]
        category["checks"] = dict.fromkeys(category["checks"], perfect)
        category.update(score=float(perfect), passed=perfect)
    behavior.update(score=float(perfect), passed=perfect, failures=[] if perfect else [{"check": "all"}])
    judge = {
        "scoring_version": 2,
        "score": float(perfect),
        "passed": perfect,
        "reason": "Observed score boundary",
        "findings": [] if perfect else [{"issue": "not done", "evidence": "trace", "deduction": 1.0}],
    }
    breakdown = json.loads(objects["root/evaluation-breakdown.json"])
    breakdown.update(
        task_completion=behavior["task_completion"],
        protocol_compliance=behavior["protocol_compliance"],
        llm_judgment=judge,
        failures=behavior["failures"],
        overall_score=float(perfect),
        overall_passed=perfect,
    )
    for name, value in (("behavior", behavior), ("answer-judge", judge), ("evaluation-breakdown", breakdown)):
        objects[f"root/{name}.json"] = json.dumps(value).encode()
    change_evidence(objects, "trial/result.json", ["verifier_result", "rewards", "reward"], float(perfect))
    validate_attempt(objects, "uid", "control", "mock")


def test_nested_judge_boolean_cannot_impersonate_numeric_zero():
    objects = numeric_evidence()
    judge = json.loads(objects["root/answer-judge.json"])
    judge.update(score=0.0, findings=[{"issue": "failed", "evidence": "trace", "deduction": 1.0}])
    objects["root/answer-judge.json"] = json.dumps(judge).encode()
    breakdown = json.loads(objects["root/evaluation-breakdown.json"])
    breakdown.update(llm_judgment=deepcopy(judge), overall_score=0.5)
    breakdown["llm_judgment"]["score"] = False
    objects["root/evaluation-breakdown.json"] = json.dumps(breakdown).encode()
    change_evidence(objects, "trial/result.json", ["verifier_result", "rewards", "reward"], 0.5)
    with pytest.raises(RuntimeError, match="bounded numeric"):
        validate_attempt(objects, "uid", "control", "mock")


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "empty-model",
        "partial-trace",
        "wrong-result",
        "missing-context",
        "hidden-assessment",
        "unknown-format",
    ],
)
def test_v2_requires_full_trace_judge_request(problem):
    objects = numeric_evidence()
    key = "root/llm-judge-input.json"
    if problem == "missing":
        del objects[key]
    else:
        payload = json.loads(objects[key])
        supplied = json.loads(payload["messages"][1]["content"])
        if problem == "empty-model":
            payload["model"] = " "
        elif problem == "partial-trace":
            supplied["full_native_trace"] = json.dumps(supplied["final_result"]) + "\n"
        elif problem == "wrong-result":
            supplied["final_result"]["result"] = "Invented answer"
        elif problem == "missing-context":
            supplied["task_context"].pop("task_request")
        elif problem == "hidden-assessment":
            supplied["expected_passed"] = True
        else:
            supplied["native_trace_format"] = "guessed-format"
        payload["messages"][1]["content"] = json.dumps(supplied)
        objects[key] = json.dumps(payload).encode()
    with pytest.raises(RuntimeError):
        validate_attempt(objects, "uid", "control", "mock")


@pytest.mark.parametrize("failed_turn", [False, True])
@pytest.mark.parametrize("trailing_empty", [False, True])
def test_v2_explicit_codex_trace_uses_actual_events(failed_turn, trailing_empty):
    objects = numeric_evidence()
    payload = json.loads(objects["root/llm-judge-input.json"])
    supplied = json.loads(payload["messages"][1]["content"])
    final = {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "Actual answer"}}
    rows = [final]
    if trailing_empty:
        rows.append({"type": "item.completed", "item": {"id": "empty", "type": "agent_message", "text": " "}})
    rows.append({"type": "turn.failed" if failed_turn else "turn.completed"})
    raw = "".join(json.dumps(row) + "\n" for row in rows)
    supplied.update(native_trace_format="codex", final_result=final, full_native_trace=raw)
    objects["root/agent/codex.txt"] = raw.encode()
    del objects["root/agent/claude-code.txt"]
    payload["messages"][1]["content"] = json.dumps(supplied)
    objects["root/llm-judge-input.json"] = json.dumps(payload).encode()
    if failed_turn:
        with pytest.raises(RuntimeError, match="Codex trace"):
            validate_attempt(objects, "uid", "control", "mock")
    else:
        validate_attempt(objects, "uid", "control", "mock")
