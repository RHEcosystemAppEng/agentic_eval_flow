"""Exercise lifecycle failure boundaries, native runner configuration and real MCP transport."""

import asyncio
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
import yaml
from pydantic import ValidationError

from abevalflow import mcp_ci as ci
from abevalflow.schemas import McpConfig

REPO = Path(__file__).resolve().parents[1]
IMAGE = "example.invalid/mock@sha256:" + "a" * 64


def test_targets_are_opt_in_and_exclusive():
    assert McpConfig(credentials_secret="existing").mock is None
    assert McpConfig(mock="new-mock").credentials_secret is None
    for data in [{}, {"credentials_secret": "a", "mock": "b"}, {"mock": "../escape"}]:
        with pytest.raises(ValidationError):
            McpConfig.model_validate(data)


def test_catalog_is_config_only_and_pinned(tmp_path):
    for name in ["first", "second"]:
        (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump({"image": IMAGE, "port": 8765, "path": "/other"}))
        assert ci.descriptor(name, tmp_path).port == 8765
    with pytest.raises(ValueError):
        ci.descriptor("../escape", tmp_path)
    for patch in [{"image": "example:latest"}, {"command": ["sh"]}, {"path": "/mcp?token=x"}]:
        with pytest.raises(ValidationError):
            ci.MockDescriptor.model_validate({"image": IMAGE, **patch})


def test_resources_isolate_attempts_and_expose_no_controller_credentials():
    desc = ci.MockDescriptor(image=IMAGE)
    first = ci.resource_specs(desc, "run-one", "attempt", "ns", "pipeline-one")
    second = ci.resource_specs(desc, "run-two", "attempt", "ns", "pipeline-two")
    assert first[0] != second[0]
    pod, service, policy, egress = first[1:]
    assert pod["spec"]["automountServiceAccountToken"] is False
    assert "volumes" not in pod["spec"]
    assert "ownerReferences" not in pod["metadata"]
    assert service["spec"]["selector"] == pod["metadata"]["labels"]
    assert policy["spec"]["egress"] == []
    assert (
        policy["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"]["tekton.dev/pipelineRun"]
        == "pipeline-one"
    )
    assert egress["spec"]["podSelector"]["matchLabels"]["tekton.dev/pipelineTask"] == "evaluate"
    assert all("ipBlock" not in str(rule) for rule in egress["spec"]["egress"])


def test_handoff_rejects_stale_missing_and_invalid_paths(tmp_path):
    results = tmp_path / "out"
    results.mkdir()
    output = tmp_path / "tekton"
    output.mkdir()
    values = {
        "treatment-mean-reward": "1",
        "control-mean-reward": "0",
        "recommendation": "pass",
        "results-dir": str(results),
    }
    ci.handoff(tmp_path, "uid", "mcpchecker", values)
    ci.bridge(tmp_path, "uid", "mcpchecker", output)
    assert (output / "recommendation").read_text() == "pass"
    with pytest.raises(ValueError):
        ci.bridge(tmp_path, "uid", "harbor", output)
    with pytest.raises(FileNotFoundError):
        ci.bridge(tmp_path, "other", "mcpchecker", output)
    with pytest.raises(ValueError):
        ci.handoff(tmp_path, "uid", "mcpchecker", {**values, "results-dir": "/etc"})
    with pytest.raises(ValueError):
        ci.handoff(tmp_path, "uid", "mcpchecker", {**values, "treatment-mean-reward": "nan"})


class FakeCluster:
    namespace = "test"

    def __init__(self, fail_create=None):
        self.custom = NS(
            get_namespaced_custom_object=lambda *a: {
                "metadata": {"uid": a[-1] if a[-1] != "run" else "uid", "resourceVersion": "1"}
            }
        )
        self.custom.list_namespaced_custom_object = lambda *a, **k: {"items": []}
        self.custom.patch_namespaced_custom_object = lambda *a: None
        self.created = []
        self.deleted = []
        self.fail_create = fail_create
        self.core = NS(read_namespaced_pod_log=lambda *a, **k: self.log(None))

    def create(self, obj):
        if len(self.created) == self.fail_create:
            raise RuntimeError("API failed")
        self.created.append(obj)

    def list(self, kind, selector):
        if "tekton.dev/" in selector:
            return []
        return [
            NS(metadata=NS(name=o["metadata"]["name"]), spec=NS(containers=[NS(name="mock", image=IMAGE)]))
            for o in self.created
            if o["kind"] == kind and (kind, o["metadata"]["name"]) not in self.deleted
        ]

    def log(self, pod):
        return '{"event":"tool_call","tool":"get_cve_by_id"}\n'

    def delete(self, kind, name):
        self.deleted.append((kind, name))


def test_partial_setup_retains_identity_and_creates_no_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "descriptor", lambda _: ci.MockDescriptor(image=IMAGE))
    cluster = FakeCluster(fail_create=2)
    with pytest.raises(RuntimeError):
        ci.setup(cluster, tmp_path, "uid", "a", "run", "mock")
    assert json.loads((ci.paths(tmp_path, "uid") / "target.json").read_text())["uid"] == "uid"
    assert not cluster.deleted
    assert not (ci.paths(tmp_path, "uid") / "result.json").exists()


def test_protocol_readiness_and_timeout_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "descriptor", lambda _: ci.MockDescriptor(image=IMAGE))

    async def ready(_):
        return ["get_cve_by_id"]

    monkeypatch.setattr(ci, "probe", ready)
    ci.setup(FakeCluster(), tmp_path, "uid", "a", "run", "mock")
    assert json.loads((ci.paths(tmp_path, "uid") / "readiness.json").read_text())["tools"] == ["get_cve_by_id"]
    with pytest.raises(TimeoutError):
        ci.setup(FakeCluster(), tmp_path, "other", "a", "run", "mock", timeout=0)
    assert json.loads((ci.paths(tmp_path, "other") / "readiness.json").read_text())["ready"] is False


class MemoryArchive:
    saved = {}
    fail = False

    def __init__(self, prefix):
        self.prefix = prefix

    def put(self, key, value):
        if self.fail:
            raise RuntimeError("storage unavailable")
        self.saved[key] = value

    def commit(self, uid):
        self.saved["archive-complete"] = uid


def test_archive_failure_preserves_resources_and_retry_is_idempotent(tmp_path, monkeypatch):
    desc = ci.MockDescriptor(image=IMAGE)
    cluster = FakeCluster()
    for obj in ci.resource_specs(desc, "uid", "a", "run", "run")[1:]:
        cluster.create(obj)
    MemoryArchive.saved = {}
    MemoryArchive.fail = True
    with pytest.raises(RuntimeError):
        ci.finalize(cluster, tmp_path, "uid", "run", archive_factory=MemoryArchive)
    assert not cluster.deleted
    MemoryArchive.fail = False
    ci.finalize(cluster, tmp_path, "uid", "run", archive_factory=MemoryArchive)
    assert MemoryArchive.saved["archive-complete"] == "uid"
    assert len(cluster.deleted) == 4
    ci.finalize(cluster, tmp_path, "uid", "run", archive_factory=MemoryArchive)
    assert len(cluster.deleted) == 4


def test_native_runner_models_managed_endpoint_and_valid_negative(tmp_path, monkeypatch):
    source = tmp_path / "submissions/probe"
    source.mkdir(parents=True)
    (source / "metadata.yaml").write_text("mcp:\n  mock: cve-smoke\n")
    (source / "eval.yaml").write_text(
        yaml.safe_dump({"config": {"agent": {"type": "builtin.llm-agent"}, "taskSets": []}})
    )
    ci.atomic_json(
        ci.paths(tmp_path, "uid") / "target.json",
        {"uid": "uid", "mock": "cve-smoke", "endpoint": "http://isolated/mcp"},
    )
    # Native failed assertion is a valid evaluation, including MCPChecker's nonzero exit.
    raw = json.loads((REPO / "tests/fixtures/mcpchecker/assertion-failure.json").read_text())

    def checker(args, **kwargs):
        assert yaml.safe_load(Path(args[2]).read_text())["config"]["agent"]["model"] == "openai:cluster-model"
        mcp = yaml.safe_load(Path(args[4]).read_text())
        assert mcp["mcpServers"]["mock"]["url"] == "http://isolated/mcp"
        assert "command" not in str(mcp)
        kwargs["stdout"].write("header\n" + json.dumps(raw))
        return NS(returncode=1)

    monkeypatch.setattr(subprocess, "run", checker)
    ci.evaluate(tmp_path, "uid", "run", "probe", "probe", "openai:cluster-model", "openai:judge", 30)
    data = json.loads((ci.paths(tmp_path, "uid") / "result.json").read_text())
    assert data["results"]["recommendation"] == "fail"
    assert not (ci.paths(tmp_path, "uid") / "mcp-config.yaml").exists()
    assert json.loads((tmp_path / "eval-results/probe/process-status.json").read_text())["exit_code"] == 1


def test_extract_rejects_bad_or_missing_native_results():
    for text in ["garbage", '{"error":"provider failed"}']:
        with pytest.raises(ValueError):
            ci.extract_json(text)
    assert ci.extract_json('header\n{"results":[]}')["results"] == []
    assert ci.duration_seconds("10m0s") == 600


def test_real_fixture_transport_and_audit(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    # Run the actual fixture in an isolated process, changing only its bind port.
    source = (REPO / "containers/mcp-mocks/cve-smoke/server.py").read_text().replace("port=8000", f"port={port}")
    source = source.replace(
        'Path(__file__).with_name("cve.json")', repr(str(REPO / "containers/mcp-mocks/cve-smoke/cve.json"))
    )
    source = source.replace(
        "json.loads(" + repr(str(REPO / "containers/mcp-mocks/cve-smoke/cve.json")) + ".read_text())",
        "json.loads(Path(" + repr(str(REPO / "containers/mcp-mocks/cve-smoke/cve.json")) + ").read_text())",
    )
    script = tmp_path / "fixture.py"
    script.write_text(source)
    log = (tmp_path / "audit.log").open("w")
    process = subprocess.Popen([sys.executable, "-u", str(script)], stdout=log, stderr=log)

    async def query():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as transport:
            async with ClientSession(transport[0], transport[1]) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert [x.name for x in tools.tools] == ["get_cve_by_id"]
                result = await session.call_tool("get_cve_by_id", {"cve_id": "CVE-2016-10739"})
                assert "glibc" in str(result)

    try:
        stop = time.monotonic() + 15
        while True:
            try:
                asyncio.run(asyncio.wait_for(query(), timeout=5))
                break
            except Exception:
                if process.poll() is not None or time.monotonic() > stop:
                    raise
                time.sleep(0.2)
    finally:
        process.terminate()
        process.wait(timeout=10)
        log.close()
    assert '"event": "tool_call"' in (tmp_path / "audit.log").read_text()


@pytest.fixture
def runner_workspace(tmp_path):
    source = tmp_path / "submissions/probe"
    source.mkdir(parents=True)
    (source / "metadata.yaml").write_text("mcp:\n  mock: cve-smoke\n")
    (source / "eval.yaml").write_text(yaml.safe_dump({"config": {"agent": {"type": "builtin.llm-agent"}}}))
    ci.atomic_json(
        ci.paths(tmp_path, "uid") / "target.json",
        {"uid": "uid", "mock": "cve-smoke", "endpoint": "http://isolated/mcp"},
    )
    return tmp_path, source


@pytest.mark.parametrize(
    "flag,value",
    [
        ("taskError", "HTTP401"),
        ("timedOut", True),
        ("agentExecutionError", True),
        ("taskJudgeError", "provider unavailable"),
    ],
)
def test_native_infrastructure_failures_never_publish_score(runner_workspace, monkeypatch, flag, value):
    workspace, _ = runner_workspace
    raw = json.loads((REPO / "tests/fixtures/mcpchecker/assertion-failure.json").read_text())
    raw["results"][0][flag] = value

    def checker(args, **kwargs):
        kwargs["stdout"].write(json.dumps(raw))
        return NS(returncode=1)

    monkeypatch.setattr(subprocess, "run", checker)
    with pytest.raises(RuntimeError, match="infrastructure"):
        ci.evaluate(workspace, "uid", "run", "probe", "probe", "openai:a", "openai:j", 30)
    assert not (ci.paths(workspace, "uid") / "result.json").exists()
    assert (workspace / "eval-results/probe/mcpchecker-out.json").exists()


def test_external_native_and_legacy_refs_preserve_http_config(runner_workspace, monkeypatch):
    workspace, source = runner_workspace
    (source / "metadata.yaml").write_text("mcp:\n  credentials_secret: existing\n")
    (source / "mcp-config.yaml").write_text(
        "mcpServers:\n  remote:\n    url: ${MCP_URL}\n    headers:\n"
        '      Authorization: "Bearer ${MCP_BEARER_TOKEN}"\n    note: "price $5"\n'
    )
    monkeypatch.setenv("MCP_URL", "http://external/mcp")
    monkeypatch.delenv("MCP_BEARER_TOKEN", raising=False)
    raw = json.loads((REPO / "tests/fixtures/mcpchecker/assertion-failure.json").read_text())

    def checker(args, **kwargs):
        mcp = yaml.safe_load(Path(args[4]).read_text())["mcpServers"]["remote"]
        assert args[-2] == "--default-task-timeout"
        assert args[-1] == expected_timeout
        assert kwargs["timeout"] is None
        assert mcp["url"] == "http://external/mcp"
        assert mcp["headers"]["Authorization"] == "Bearer "
        assert mcp["note"] == "price $5"
        config = yaml.safe_load(Path(args[2]).read_text())["config"]
        assert config["agent"]["model"] == "openai:agent"
        assert config["llmJudge"]["ref"]["model"] == "openai:judge"
        kwargs["stdout"].write(json.dumps(raw))
        return NS(returncode=1)

    monkeypatch.setattr(subprocess, "run", checker)
    for config, expected_timeout in [
        ({"config": {"agent": {"type": "builtin.llm-agent"}}}, "30s"),
        ({"spec": {"agent": {"model": "old"}, "judge": {"model": "old"}, "tasks": [], "taskTimeout": 300}}, "300s"),
    ]:
        (source / "eval.yaml").write_text(yaml.safe_dump(config))
        ci.evaluate(workspace, "uid", "run", "probe", "probe", "openai:agent", "openai:judge", 30)


def test_ae_handoff_preserves_single_variant_na_control(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    values = {
        "treatment-mean-reward": "0.8",
        "control-mean-reward": "N/A",
        "recommendation": "pass",
        "results-dir": str(output),
    }
    ci.handoff(tmp_path, "uid", "aeh", values)
    ci.bridge(tmp_path, "uid", "aeh", output)
    assert (output / "control-mean-reward").read_text() == "N/A"
    with pytest.raises(ValueError):
        ci.handoff(tmp_path, "uid", "mcpchecker", values)


def test_log_snapshot_handles_real_kubernetes_dates_and_only_never_started_400(tmp_path):
    from datetime import UTC, datetime

    from kubernetes.client import V1Container, V1ObjectMeta, V1Pod, V1PodSpec, V1PodStatus
    from kubernetes.client.exceptions import ApiException

    pod = V1Pod(
        metadata=V1ObjectMeta(name="pending"),
        spec=V1PodSpec(containers=[V1Container(name="mock", image=IMAGE)]),
        status=V1PodStatus(phase="Pending", start_time=datetime.now(UTC)),
    )

    def unavailable(*args, **kwargs):
        raise ApiException(status=400)

    cluster = NS(namespace="test", core=NS(read_namespaced_pod_log=unavailable))
    ci.collect_logs(cluster, [pod], tmp_path)
    assert "never started" in (tmp_path / "pod-logs/pending.log").read_text()
    assert json.loads((tmp_path / "pod-status/pending.json").read_text())["status"]["start_time"]
    pod.status.phase = "Running"
    with pytest.raises(ApiException):
        ci.collect_logs(cluster, [pod], tmp_path)


def test_same_name_run_and_recreated_taskrun_never_select_new_pod():
    pod = NS(metadata=NS(owner_references=[NS(kind="TaskRun", name="task", uid="old-task")]))
    task = {"metadata": {"uid": "new-task", "ownerReferences": [{"uid": "old-run"}]}}
    cluster = NS(namespace="test", list=lambda *a: [pod], custom=NS(get_namespaced_custom_object=lambda *a: task))
    assert ci.task_pods(cluster, "old-run", "reused-name") == []
    task["metadata"]["uid"] = "old-task"
    assert ci.task_pods(cluster, "old-run", "reused-name") == [pod]
    assert ci.task_pods(cluster, "new-run", "reused-name") == []


def test_sweep_continues_after_malformed_and_failed_orphans(monkeypatch):
    claims = [
        NS(metadata=NS(labels={ci.LABEL: uid}, annotations=ann))
        for uid, ann in [
            ("bad", {}),
            ("outage", {"abevalflow.io/pipeline-run": "old1"}),
            ("good", {"abevalflow.io/pipeline-run": "old2"}),
        ]
    ]
    cluster = NS(
        namespace="test",
        core=NS(list_namespaced_persistent_volume_claim=lambda *a, **k: NS(items=claims)),
        custom=NS(
            get_namespaced_custom_object=lambda *a: {"metadata": {"uid": "different"}},
            list_namespaced_custom_object=lambda *a: {"items": []},
        ),
    )
    attempts, released = [], []

    def finish(cluster, uid, *a):
        attempts.append(uid)
        if uid == "outage":
            raise RuntimeError("storage outage")

    monkeypatch.setattr(ci, "finish", finish)
    monkeypatch.setattr(ci, "release_workspace", lambda cluster, uid, *a: released.append(uid))
    with pytest.raises(RuntimeError, match="bad.*outage"):
        ci.sweep(cluster, IMAGE)
    assert attempts == ["outage", "good"]
    assert released == ["good"]


def test_finish_recreates_failed_archive_worker_and_waits_for_mount_release(monkeypatch):
    from kubernetes import client
    from kubernetes.client.exceptions import ApiException

    class Batch:
        exists = True
        failed = True
        created = 0
        deleted = 0

        def create_namespaced_job(self, *a):
            if self.exists:
                raise ApiException(status=409)
            self.exists, self.failed = True, False
            self.created += 1

        def read_namespaced_job(self, *a):
            if not self.exists:
                raise ApiException(status=404)
            return NS(status=NS(failed=self.failed, succeeded=not self.failed))

        def delete_namespaced_job(self, *a, **k):
            self.exists = False
            self.deleted += 1

    batch = Batch()
    pvc = NS(
        metadata=NS(
            name="evidence",
            labels={ci.LABEL: "uid"},
            owner_references=[],
            annotations={"abevalflow.io/evidence-node": "worker"},
        )
    )
    cluster = NS(
        namespace="test",
        list=lambda *a: [],
        core=NS(
            list_namespaced_persistent_volume_claim=lambda *a, **k: NS(items=[pvc]),
            patch_namespaced_persistent_volume_claim=lambda *a: None,
        ),
    )
    monkeypatch.setattr(client, "BatchV1Api", lambda: batch)
    ci.finish(cluster, "uid", "run", IMAGE)
    assert batch.created == 1 and batch.deleted == 2 and not batch.exists


def test_completed_handoff_requires_raw_evidence_before_cleanup(tmp_path):
    root = ci.paths(tmp_path, "uid")
    ci.atomic_json(root / "context.json", {"submission": "probe"})
    ci.atomic_json(root / "result.json", {"uid": "uid"})
    cluster = FakeCluster()
    with pytest.raises(RuntimeError, match="evidence"):
        ci.finalize(cluster, tmp_path, "uid", "run", archive_factory=MemoryArchive)
    assert not cluster.deleted


def test_dedicated_profile_has_one_evaluation_and_no_irrelevant_stages():
    spec = yaml.safe_load((REPO / "pipeline/pipelines/ci-pipeline-mcpchecker.yaml").read_text())["spec"]
    assert [task["name"] for task in spec["tasks"]] == ["prepare", "test", "start-mcp", "evaluate", "store"]
    tasks = {task["name"]: task for task in spec["tasks"]}
    assert tasks["evaluate"]["taskRef"]["name"] == "evaluate-mcpchecker"
    assert tasks["evaluate"]["runAfter"] == ["start-mcp"]
    assert tasks["store"]["runAfter"] == ["evaluate"]
    assert not any("when" in task for task in spec["tasks"])
    assert "eval-engine" not in {param["name"] for param in spec["params"]}
    assert all("tasks.evaluate.results." in result["value"] for result in spec["results"])
    task = yaml.safe_load((REPO / "pipeline/tasks/components/evaluate-mcpchecker.yaml").read_text())["spec"]
    assert {r["name"] for r in task["results"]} == {r["name"] for r in spec["results"]}
    assert [step["args"][0] for step in task["steps"]] == ["evaluate", "bridge"]
    assert len(spec["finally"]) == 1 and spec["finally"][0]["taskRef"]["name"] == "cleanup-mcp"


def test_profile_run_selects_lifecycle_and_tokenless_evaluation():
    run = yaml.safe_load((REPO / "pipeline/runs/mcpchecker-pipelinerun.yaml").read_text())["spec"]
    assert run["pipelineRef"]["name"] == "abevalflow-pipeline-mcpchecker"
    overrides = {t["pipelineTaskName"]: t for t in run["taskRunSpecs"]}
    assert set(overrides) == {"start-mcp", "evaluate", "cleanup"}
    assert overrides["evaluate"]["serviceAccountName"] == "mcpchecker-evaluator"
    assert overrides["evaluate"]["podTemplate"]["automountServiceAccountToken"] is False
    assert overrides["start-mcp"]["serviceAccountName"] == overrides["cleanup"]["serviceAccountName"] == "mcp-lifecycle"
    assert "volumeClaimTemplate" in run["workspaces"][0]


@pytest.mark.parametrize("exit_code", [2, -9])
def test_abnormal_exit_is_not_a_valid_negative(runner_workspace, monkeypatch, exit_code):
    workspace, _ = runner_workspace
    raw = json.loads((REPO / "tests/fixtures/mcpchecker/assertion-failure.json").read_text())

    def checker(args, **kwargs):
        kwargs["stdout"].write(json.dumps(raw))
        return NS(returncode=exit_code)

    monkeypatch.setattr(subprocess, "run", checker)
    with pytest.raises(RuntimeError, match="abnormally"):
        ci.evaluate(workspace, "uid", "run", "probe", "probe", "openai:a", "openai:j", 30)
    assert not (ci.paths(workspace, "uid") / "result.json").exists()


def test_completed_wrong_answer_is_valid_failed_evaluation(runner_workspace, monkeypatch):
    workspace, _ = runner_workspace
    raw = json.loads((REPO / "tests/fixtures/mcpchecker/assertion-failure.json").read_text())
    raw["results"][0].update(taskPassed=False, taskError="one or more verification steps failed")

    def checker(args, **kwargs):
        kwargs["stdout"].write(json.dumps(raw))
        return NS(returncode=1)

    monkeypatch.setattr(subprocess, "run", checker)
    ci.evaluate(workspace, "uid", "run", "probe", "probe", "openai:a", "openai:j", 30)
    assert json.loads((ci.paths(workspace, "uid") / "result.json").read_text())["results"]["recommendation"] == "fail"


def test_stop_producers_before_collecting_final_logs(monkeypatch):
    pod = NS(metadata=NS(name="mock"), status=NS(phase="Running"))
    events = []
    cluster = NS(
        namespace="test",
        core=NS(
            patch_namespaced_pod=lambda *a: events.append(a[2]),
            read_namespaced_pod=lambda *a: NS(metadata=pod.metadata, status=NS(phase="Failed")),
        ),
    )
    monkeypatch.setattr(ci.time, "sleep", lambda _: None)
    assert ci.quiesce(cluster, [pod])[0].status.phase == "Failed"
    assert events == [{"spec": {"activeDeadlineSeconds": 1}}]


def test_untrusted_submission_is_an_argument_never_shell_source(tmp_path):
    for name in ["start-mcp", "evaluate-mcpchecker"]:
        task = yaml.safe_load((REPO / f"pipeline/tasks/components/{name}.yaml").read_text())
        step = task["spec"]["steps"][0]
        assert "script" not in step
        assert step["command"] == ["python", "-m", "abevalflow.mcp_ci"]
    with pytest.raises(ValueError):
        ci.submission_path(tmp_path, "../../outside")
    escaped = tmp_path / "submissions/escaped"
    escaped.parent.mkdir()
    escaped.symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        ci.submission_path(tmp_path, "escaped")


def test_non_mcp_complete_manifest_does_not_require_mcp_output(tmp_path):
    root = ci.paths(tmp_path, "uid")
    ci.atomic_json(root / "context.json", {"submission": "probe"})
    ci.atomic_json(root / "result.json", {"uid": "uid", "engine": "ase"})
    report = tmp_path / "reports/probe/report.json"
    ci.atomic_json(report, {"summary": "non-MCP report"})
    MemoryArchive.saved = {}
    ci.finalize(FakeCluster(), tmp_path, "uid", "run", archive_factory=MemoryArchive)
    assert MemoryArchive.saved["archive-complete"] == "uid"
    assert "workspace/reports/probe/report.json" in MemoryArchive.saved


def test_unpinned_workspace_is_discovered_by_owner_uid():
    pvc = NS(metadata=NS(labels=None, owner_references=[NS(uid="original")]))
    other = NS(metadata=NS(labels=None, owner_references=[NS(uid="new-run")]))
    cluster = NS(namespace="test", core=NS(list_namespaced_persistent_volume_claim=lambda *a: NS(items=[pvc, other])))
    assert ci.workspaces(cluster, "original") == [pvc]


def test_mcp_profile_is_selected_explicitly_without_changing_webhook():
    trigger = yaml.safe_load((REPO / "pipeline/triggers/trigger-template.yaml").read_text())
    assert trigger["spec"]["resourcetemplates"][0]["spec"]["pipelineRef"]["name"] == "abevalflow-pipeline"
    run = yaml.safe_load((REPO / "pipeline/runs/mcpchecker-pipelinerun.yaml").read_text())
    assert run["spec"]["pipelineRef"]["name"] == "abevalflow-pipeline-mcpchecker"


def test_static_shared_pvc_is_preserved_before_any_label_or_delete(tmp_path, monkeypatch):
    from kubernetes.client import V1ObjectMeta, V1PersistentVolumeClaim

    monkeypatch.setenv("POD_NAME", "start")
    pvc = V1PersistentVolumeClaim(metadata=V1ObjectMeta(name="shared"))
    patched = []
    pod = NS(spec=NS(volumes=[NS(persistent_volume_claim=NS(claim_name="shared"))]))
    cluster = NS(
        namespace="test",
        core=NS(
            read_namespaced_pod=lambda *a: pod,
            read_namespaced_persistent_volume_claim=lambda *a: pvc,
            patch_namespaced_persistent_volume_claim=lambda *a: patched.append(a),
        ),
    )
    with pytest.raises(ValueError, match="shared PVCs are preserved"):
        ci.pin_workspace(cluster, tmp_path, "uid", "run")
    assert not patched


def test_database_secret_is_optional_for_general_archive_workers():
    env = {item["name"]: item for item in ci.storage_env()}
    assert env["DATABASE_URL"]["valueFrom"]["secretKeyRef"]["optional"] is True
    assert env["MINIO_SECRET_KEY"]["valueFrom"]["secretKeyRef"]["optional"] is False


def test_protected_but_not_pinned_run_is_recoverable(monkeypatch):
    calls = []
    metadata = {"uid": "early", "name": "run", "finalizers": [ci.PROTECTION]}
    run = {"metadata": metadata, "status": {"completionTime": "now"}}
    cluster = NS(
        namespace="test",
        core=NS(list_namespaced_persistent_volume_claim=lambda *a, **k: NS(items=[])),
        custom=NS(
            list_namespaced_custom_object=lambda *a: {"items": [run]}, get_namespaced_custom_object=lambda *a: run
        ),
    )
    monkeypatch.setattr(ci, "finish", lambda *a: calls.append("archive"))
    monkeypatch.setattr(ci, "release_workspace", lambda *a: calls.append("release"))
    ci.sweep(cluster, IMAGE)
    assert calls == ["archive", "release"]


def test_mcp_suite_keeps_existing_three_hour_tekton_deadline():
    pipeline = yaml.safe_load((REPO / "pipeline/pipelines/ci-pipeline-mcpchecker.yaml").read_text())
    task = next(t for t in pipeline["spec"]["tasks"] if t["name"] == "evaluate")
    assert task["timeout"] == "3h"


def test_cancelled_or_retried_task_requires_final_log_and_identity(tmp_path):
    task = {
        "metadata": {
            "uid": "task-uid",
            "name": "task",
            "ownerReferences": [{"uid": "uid"}],
            "labels": {"tekton.dev/memberOf": "tasks"},
        },
        "status": {"podName": "cancelled-pod", "retriesStatus": [{"podName": "first-pod"}]},
    }
    run = {"metadata": {"uid": "uid"}, "status": {"childReferences": [{"kind": "TaskRun", "name": "task"}]}}
    cluster = NS(
        namespace="test",
        custom=NS(
            get_namespaced_custom_object=lambda *a: run if a[-2] == "pipelineruns" else task,
            list_namespaced_custom_object=lambda *a: {"items": [task]},
        ),
    )
    with pytest.raises(RuntimeError, match="missing final DAG"):
        ci.require_task_evidence(cluster, "uid", "run", tmp_path)
    for name in ["cancelled-pod", "first-pod"]:
        log = tmp_path / "pod-logs" / (name + ".log")
        log.parent.mkdir(exist_ok=True)
        log.write_text("final log")
        ci.atomic_json(
            tmp_path / "pod-status" / (name + ".json"), {"taskrun_uid": "task-uid", "status": {"phase": "Failed"}}
        )
    ci.require_task_evidence(cluster, "uid", "run", tmp_path)
    ci.atomic_json(
        tmp_path / "pod-status/cancelled-pod.json", {"taskrun_uid": "task-uid", "status": {"phase": "Running"}}
    )
    with pytest.raises(RuntimeError, match="missing final DAG"):
        ci.require_task_evidence(cluster, "uid", "run", tmp_path)

    # An owned TaskRun remains discoverable before childReferences reconciliation.
    run["status"]["childReferences"] = []
    with pytest.raises(RuntimeError, match="cancelled-pod"):
        ci.require_task_evidence(cluster, "uid", "run", tmp_path)
    task["status"] = {}
    with pytest.raises(RuntimeError, match="Pod identity unavailable"):
        ci.require_task_evidence(cluster, "uid", "run", tmp_path)
    task["status"] = {"podName": "first-pod", "retriesStatus": [{}]}
    with pytest.raises(RuntimeError, match="Pod identity unavailable"):
        ci.require_task_evidence(cluster, "uid", "run", tmp_path)


def test_release_removes_run_protection_only_after_confirmed_pvc_absence(monkeypatch):
    events = []
    cluster = NS(namespace="test", apps=NS(list_namespaced_stateful_set=lambda *a: NS(items=[])))
    monkeypatch.setattr(ci, "task_pods", lambda *a: [])
    monkeypatch.setattr(ci, "workspaces", lambda *a: events.append("pvc-absence-checked") or [])
    monkeypatch.setattr(ci, "protect_run", lambda *a: events.append("unprotect"))
    assert ci.release_workspace(cluster, "uid", "run") is False
    assert events == ["pvc-absence-checked", "pvc-absence-checked", "unprotect"]


def test_finish_after_pvc_release_reuses_verified_archive(monkeypatch):
    events = []
    cluster = NS(namespace="test")
    monkeypatch.setattr(ci, "workspaces", lambda *a: [])
    monkeypatch.setattr(ci, "Archive", lambda *a: NS(verified_cleanup=lambda uid: events.append(uid) or True))
    monkeypatch.setattr(ci, "finalize", lambda *a: pytest.fail("must not recollect deleted Pod logs"))
    ci.finish(cluster, "uid", "run", IMAGE)
    assert events == ["uid"]


def test_committed_archive_reuse_checks_integrity_identity_and_inventory():
    import hashlib
    import io

    prefix = "mcp-runs/uid"
    inventory_key = prefix + "/workspace/task-inventory.json"
    inventory = b'{"uid":"uid","tasks":[],"missing":[]}'
    objects = {
        inventory_key: inventory,
        prefix + "/cleanup.json": b'{"uid":"uid","deleted":true}',
    }
    manifest = {
        "uid": "uid",
        "complete": True,
        "objects": [{"key": inventory_key, "bytes": len(inventory), "sha256": hashlib.sha256(inventory).hexdigest()}],
    }

    class Response(io.BytesIO):
        def release_conn(self):
            pass

    archive = object.__new__(ci.Archive)
    archive.bucket, archive.prefix = "test", prefix
    archive.client = NS(
        get_object=lambda bucket, key: Response(
            json.dumps(manifest).encode() if key.endswith("/archive-complete.json") else objects[key]
        )
    )
    assert archive.verified_cleanup("uid")
    objects[inventory_key] = b"tampered"
    with pytest.raises(RuntimeError, match="read-back mismatch"):
        archive.verified_cleanup("uid")
    objects[inventory_key] = inventory
    manifest["uid"] = "other"
    with pytest.raises(ValueError, match="identity"):
        archive.verified_cleanup("uid")
    manifest["uid"] = "uid"
    manifest["objects"][0]["key"] = "another-run/task-inventory.json"
    with pytest.raises(ValueError, match="outside this run"):
        archive.verified_cleanup("uid")
    manifest["objects"] = []
    assert not archive.verified_cleanup("uid")
