"""Trusted lifecycle and runner support for optional MCPChecker mock targets.

The built-in model agent receives HTTP MCP tools, never this controller's API
client, mock implementation, audit, or storage credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import math
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

LABEL = "abevalflow.io/mcp-run"
ATTEMPT = "abevalflow.io/mcp-attempt"
ROLE = "abevalflow.io/mcp-role"
PROTECTION = "abevalflow.io/archive-evidence"
CATALOG = Path(__file__).resolve().parents[1] / "config/mcp-mocks"
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9-]{0,48}\Z")
DIGEST = re.compile(r"[^\s@]+@sha256:[a-f0-9]{64}\Z")


class MockDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str
    port: int = Field(default=8000, ge=1024, le=65535)
    path: str = "/mcp"
    env: dict[str, str] = Field(default_factory=dict)
    deadline_seconds: int = Field(default=3600, ge=60, le=14400)

    @field_validator("image")
    @classmethod
    def pinned(cls, value):
        if not DIGEST.fullmatch(value):
            raise ValueError("mock image must be pinned by sha256 digest")
        return value

    @field_validator("path")
    @classmethod
    def mcp_path(cls, value):
        if not re.fullmatch(r"/[A-Za-z0-9/_-]*", value):
            raise ValueError("invalid MCP path")
        return value

    @field_validator("env")
    @classmethod
    def environment(cls, value):
        if any(not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) for key in value):
            raise ValueError("invalid environment key")
        return value


def descriptor(name, catalog=CATALOG):
    if not IDENTIFIER.fullmatch(name):
        raise ValueError("invalid mock identifier")
    return MockDescriptor.model_validate(yaml.safe_load((catalog / f"{name}.yaml").read_text()))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".pending")
    temp.write_text(
        json.dumps(
            value, indent=2, default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value)
        )
        + "\n"
    )
    temp.replace(path)


def paths(workspace, uid):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,63}", uid):
        raise ValueError("invalid run UID")
    return Path(workspace) / "_mcp_runs" / uid


def handoff(workspace, uid, engine, results):
    required = {"treatment-mean-reward", "control-mean-reward", "recommendation", "results-dir"}
    if set(results) != required or results["recommendation"] not in {"pass", "fail"}:
        raise ValueError("invalid evaluation result fields")
    for key in ["treatment-mean-reward", "control-mean-reward"]:
        value = results[key]
        if key == "control-mean-reward" and value == "N/A" and engine in {"aeh", "aeh_openshell_openclaw"}:
            continue
        if not math.isfinite(float(value)):
            raise ValueError("invalid reward")
    resolved = Path(results["results-dir"]).resolve()
    if not resolved.is_relative_to(Path(workspace).resolve()) or not resolved.is_dir():
        raise ValueError("results directory must exist inside workspace")
    atomic_json(paths(workspace, uid) / "result.json", {"uid": uid, "engine": engine, "results": results})


def bridge(workspace, uid, engine, result_dir):
    data = json.loads((paths(workspace, uid) / "result.json").read_text())
    if data.get("uid") != uid or data.get("engine") != engine or set(data) != {"uid", "engine", "results"}:
        raise ValueError("stale or malformed evaluation handoff")
    handoff(workspace, uid, engine, data["results"])
    for key, value in data["results"].items():
        (Path(result_dir) / key).write_text(str(value))


def resource_specs(desc, uid, attempt, namespace, run_name):
    token = hashlib.sha256(f"{uid}:{attempt}".encode()).hexdigest()[:20]
    name = f"mcp-{token}"
    labels = {LABEL: uid, ATTEMPT: token, ROLE: "mock"}
    annotations = {"abevalflow.io/pipeline-run": run_name, "abevalflow.io/created-at": datetime.now(UTC).isoformat()}
    meta = {"name": name, "namespace": namespace, "labels": labels, "annotations": annotations}
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": meta,
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "serviceAccountName": "mcpchecker-evaluator",
            "activeDeadlineSeconds": desc.deadline_seconds,
            "terminationGracePeriodSeconds": 15,
            "containers": [
                {
                    "name": "mock",
                    "image": desc.image,
                    "imagePullPolicy": "IfNotPresent",
                    "ports": [{"containerPort": desc.port}],
                    "env": [{"name": k, "value": v} for k, v in desc.env.items()],
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {"cpu": "500m", "memory": "512Mi"},
                    },
                    "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
                }
            ],
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": meta,
        "spec": {"selector": labels, "ports": [{"port": desc.port, "targetPort": desc.port}]},
    }
    # PipelineRun's native label exists on all orchestration/evaluation Task Pods.
    run_selector = {"tekton.dev/pipelineRun": run_name}
    policy = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": meta,
        "spec": {
            "podSelector": {"matchLabels": labels},
            "policyTypes": ["Ingress", "Egress"],
            "egress": [],
            "ingress": [
                {
                    "from": [{"podSelector": {"matchLabels": run_selector}}],
                    "ports": [{"protocol": "TCP", "port": desc.port}],
                }
            ],
        },
    }
    eval_selector = {**run_selector, "tekton.dev/pipelineTask": "evaluate"}
    evaluator_policy = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {**meta, "name": name + "-eval"},
        "spec": {
            "podSelector": {"matchLabels": eval_selector},
            "policyTypes": ["Egress"],
            "egress": [
                {
                    # OpenShift DNS can run on host-network nodes; a
                    # namespaceSelector alone does not permit that traffic.
                    "ports": [
                        {"protocol": protocol, "port": port} for port in [53, 5353] for protocol in ["UDP", "TCP"]
                    ],
                },
                {
                    "to": [
                        {"podSelector": {"matchLabels": {"app": "litellm"}}},
                        {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "litellm"}}},
                    ],
                    "ports": [{"protocol": "TCP", "port": 4000}],
                },
                {"to": [{"podSelector": {"matchLabels": labels}}], "ports": [{"protocol": "TCP", "port": desc.port}]},
            ],
        },
    }
    return name, pod, service, policy, evaluator_policy


class Cluster:
    def __init__(self, namespace):
        from kubernetes import client, config

        config.load_incluster_config()
        self.core = client.CoreV1Api()
        self.net = client.NetworkingV1Api()
        self.custom = client.CustomObjectsApi()
        self.apps = client.AppsV1Api()
        self.namespace = namespace

    def create(self, obj):
        methods = {
            "Pod": self.core.create_namespaced_pod,
            "Service": self.core.create_namespaced_service,
            "NetworkPolicy": self.net.create_namespaced_network_policy,
        }
        methods[obj["kind"]](self.namespace, obj)

    def delete(self, kind, name):
        from kubernetes.client.exceptions import ApiException

        methods = {
            "Pod": self.core.delete_namespaced_pod,
            "Service": self.core.delete_namespaced_service,
            "NetworkPolicy": self.net.delete_namespaced_network_policy,
        }
        try:
            methods[kind](name, self.namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise

    def list(self, kind, selector):
        methods = {
            "Pod": self.core.list_namespaced_pod,
            "Service": self.core.list_namespaced_service,
            "NetworkPolicy": self.net.list_namespaced_network_policy,
        }
        return methods[kind](self.namespace, label_selector=selector).items

    def log(self, pod):
        return self.core.read_namespaced_pod_log(pod.metadata.name, self.namespace, timestamps=True)


async def probe(url):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as transport:
        async with ClientSession(transport[0], transport[1]) as session:
            await session.initialize()
            result = await session.list_tools()
            return [tool.name for tool in result.tools]


def protect_run(cluster, uid, run_name, protect):
    from kubernetes.client.exceptions import ApiException

    try:
        run = cluster.custom.get_namespaced_custom_object(
            "tekton.dev", "v1", cluster.namespace, "pipelineruns", run_name
        )
    except ApiException as exc:
        if exc.status == 404 and not protect:
            return
        raise
    if run["metadata"]["uid"] != uid:
        if protect:
            raise ValueError("PipelineRun identity changed")
        return
    finalizers = [value for value in run["metadata"].get("finalizers", []) if value != PROTECTION]
    if protect:
        finalizers.append(PROTECTION)
    cluster.custom.patch_namespaced_custom_object(
        "tekton.dev",
        "v1",
        cluster.namespace,
        "pipelineruns",
        run_name,
        {"metadata": {"resourceVersion": run["metadata"]["resourceVersion"], "finalizers": finalizers}},
    )


def pin_workspace(cluster, workspace, uid, run_name):
    """Keep the only raw-evidence copy safe from PipelineRun garbage collection."""
    pod_name = os.environ.get("POD_NAME")
    if not pod_name:
        return {}
    pod = cluster.core.read_namespaced_pod(pod_name, cluster.namespace)
    claims = [v.persistent_volume_claim.claim_name for v in pod.spec.volumes if v.persistent_volume_claim]
    if len(claims) != 1:
        raise ValueError("MCP lifecycle requires exactly one source workspace PVC")
    pvc = cluster.core.read_namespaced_persistent_volume_claim(claims[0], cluster.namespace)
    owned = any(owner.uid == uid for owner in (pvc.metadata.owner_references or []))
    retry = (pvc.metadata.labels or {}).get(LABEL) == uid
    if not owned and not retry:
        raise ValueError("MCP CI requires a fresh run-owned volumeClaimTemplate; shared PVCs are preserved")
    owners = [x.to_dict() for x in (pvc.metadata.owner_references or []) if x.uid != uid]
    # API JSON field names differ from the Python model's snake_case names.
    owners = [{"apiVersion": x["api_version"], "kind": x["kind"], "name": x["name"], "uid": x["uid"]} for x in owners]
    cluster.core.patch_namespaced_persistent_volume_claim(
        claims[0],
        cluster.namespace,
        {
            "metadata": {
                "ownerReferences": owners,
                "labels": {LABEL: uid},
                "annotations": {
                    "abevalflow.io/pipeline-run": run_name,
                    "abevalflow.io/evidence-node": pod.spec.node_name,
                },
            }
        },
    )
    value = {"pvc": claims[0], "node": pod.spec.node_name, "namespace": cluster.namespace}
    atomic_json(paths(workspace, uid) / "workspace.json", value)
    return value


def setup(cluster, workspace, uid, attempt, run_name, mock, timeout=120):
    desc = descriptor(mock)
    name, *objects = resource_specs(desc, uid, attempt, cluster.namespace, run_name)
    root = paths(workspace, uid)
    (root / "result.json").unlink(missing_ok=True)
    location = json.loads((root / "workspace.json").read_text()) if (root / "workspace.json").exists() else {}
    for obj in objects:
        obj["metadata"]["annotations"].update({f"abevalflow.io/evidence-{k}": v for k, v in location.items()})
    target = {
        "uid": uid,
        "name": name,
        "mock": mock,
        "descriptor": desc.model_dump(),
        "endpoint": f"http://{name}.{cluster.namespace}.svc:{desc.port}{desc.path}",
    }
    # Identity is durable before the first create, including partial setup.
    atomic_json(root / "target.json", target)
    for obj in [objects[2], objects[3], objects[1], objects[0]]:
        cluster.create(obj)
    deadline = time.monotonic() + timeout
    error = "MCP readiness did not complete"
    while time.monotonic() < deadline:
        try:
            tools = asyncio.run(asyncio.wait_for(probe(target["endpoint"]), timeout=8))
            if not tools:
                raise ValueError("mock exposed no MCP tools")
            atomic_json(
                root / "readiness.json",
                {"ready": True, "tools": tools, "uid": uid, "name": name, "endpoint": target["endpoint"]},
            )
            return target
        except Exception as exc:
            error = type(exc).__name__
            time.sleep(2)
    atomic_json(root / "readiness.json", {"ready": False, "error": error, "uid": uid})
    raise TimeoutError(error)


def extract_json(output):
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^\s*\{", output):
        try:
            value, _ = decoder.raw_decode(output[match.start() :].lstrip())
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and ("results" in value or "taskResults" in value):
            return value
    raise ValueError("MCPChecker did not produce a results object")


def submission_path(workspace, name):
    base = (Path(workspace) / "submissions").resolve()
    path = (base / name).resolve()
    if path == base or not path.is_relative_to(base):
        raise ValueError("submission path must stay inside submissions")
    for name in ["metadata.yaml", "eval.yaml"]:
        if not (path / name).resolve().is_relative_to(path):
            raise ValueError("submission configuration must not reference files outside its directory")
    return path


def evaluate(workspace, uid, run_name, submission_dir, submission, agent_model, judge_model, timeout):
    from abevalflow.schemas import McpConfig
    from scripts.aggregate_mcpchecker import aggregate_mcpchecker_results

    root = paths(workspace, uid)
    root.mkdir(parents=True, exist_ok=True)
    (root / "result.json").unlink(missing_ok=True)
    source = submission_path(workspace, submission_dir)
    meta = yaml.safe_load((source / "metadata.yaml").read_text())
    target = McpConfig.model_validate(meta["mcp"])
    config = yaml.safe_load((source / "eval.yaml").read_text())
    default_task_timeout = timeout
    if "config" not in config and "spec" in config:
        legacy = config["spec"]
        default_task_timeout = duration_seconds(str(legacy.get("taskTimeout", timeout)))
        config["config"] = {
            "agent": {"type": "builtin.llm-agent", **legacy["agent"]},
            "llmJudge": {"ref": {"type": "builtin.llm-agent", **legacy.get("judge", {})}},
            "taskSets": [{"path": path} for path in legacy.get("tasks", [])],
        }
        config.pop("spec")
    # Native agents only for managed mode. No arbitrary local/stdio server is passed.
    if target.mock:
        if Path("/var/run/secrets/kubernetes.io/serviceaccount/token").exists():
            raise RuntimeError("managed evaluation requires a tokenless evaluator Pod")
        endpoint = json.loads((root / "target.json").read_text())
        if endpoint["uid"] != uid or endpoint["mock"] != target.mock:
            raise ValueError("stale mock endpoint manifest")
        mcp_config = {"mcpServers": {"mock": {"type": "http", "url": endpoint["endpoint"], "enableAllTools": True}}}
        if config["config"]["agent"].get("type") != "builtin.llm-agent":
            raise ValueError("managed mode requires the native MCPChecker agent")
    else:
        text = (source / "mcp-config.yaml").read_text()
        # Match envsubst: expand identifiers, including unset optional auth to
        # empty, while leaving unrelated literal dollar syntax unchanged.
        text = re.sub(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
            lambda match: os.environ.get(match[1] or match[2], ""),
            text,
        )
        mcp_config = yaml.safe_load(text)
    config["apiVersion"] = "mcpchecker/v1alpha2"
    config["config"]["agent"]["model"] = agent_model
    config["config"].setdefault("llmJudge", {}).setdefault("ref", {})["model"] = judge_model
    if target.mock:
        config["config"]["llmJudge"]["ref"]["type"] = "builtin.llm-agent"
    resolved = root / "mcp-config.yaml"
    resolved.write_text(yaml.safe_dump(mcp_config))
    resolved.chmod(0o600)
    config["config"]["mcpConfigFile"] = str(resolved)
    # Preserve task paths relative to the submission, without copying fixture data.
    run_config = source / ".mcp-ci-eval.yaml"
    run_config.write_text(yaml.safe_dump(config))
    out_dir = Path(workspace) / "eval-results" / submission
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "OPENAI_BASE_URL": os.environ.get("LITELLM_PROXY_URL", "http://litellm.ab-eval-flow.svc:4000/v1"),
    }
    started = time.monotonic()
    log_path = out_dir / "mcpchecker-process.log"
    try:
        with log_path.open("w") as log:
            result = subprocess.run(
                [
                    "mcpchecker",
                    "check",
                    str(run_config),
                    "--mcp-config-file",
                    str(resolved),
                    "-o",
                    "json",
                    "--default-task-timeout",
                    f"{default_task_timeout}s",
                ],
                cwd=source,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                # Native per-task deadlines apply; Tekton bounds the whole suite.
                timeout=None,
            )
        status = {"exit_code": result.returncode, "timed_out": False}
    except subprocess.TimeoutExpired:
        status = {"exit_code": None, "timed_out": True}
    finally:
        resolved.unlink(missing_ok=True)
        run_config.unlink(missing_ok=True)
    raw = log_path.read_text()
    for key in ["MCP_BEARER_TOKEN", "OPENAI_API_KEY"]:
        value = os.environ.get(key)
        if value and not (key == "OPENAI_API_KEY" and value == "mock"):
            raw = raw.replace(value, "[REDACTED]")
    (out_dir / "mcpchecker-process.log").write_text(raw)
    atomic_json(
        out_dir / "process-status.json",
        {
            **status,
            "duration_seconds": time.monotonic() - started,
            "uid": uid,
            "agent_model": agent_model,
            "judge_model": judge_model,
            "runtime_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    )
    if status["timed_out"]:
        raise TimeoutError("MCPChecker timed out")
    native = extract_json(raw)
    atomic_json(out_dir / "mcpchecker-out.json", native)
    tasks = native.get("results") or native.get("taskResults") or []
    infrastructure_fields = ("error", "errorMessage", "taskError", "timedOut", "agentExecutionError", "taskJudgeError")

    def is_infrastructure(task):
        for key in infrastructure_fields:
            value = task.get(key)
            if key == "taskError" and value == "one or more verification steps failed":
                continue
            if value:
                return True
        return False

    if status["exit_code"] not in {0, 1}:
        raise RuntimeError("MCPChecker terminated abnormally")
    if not tasks or any(is_infrastructure(task) for task in tasks):
        raise RuntimeError("MCPChecker reported missing tasks or infrastructure errors")
    report = aggregate_mcpchecker_results(out_dir / "mcpchecker-out.json", submission, pipeline_run_id=run_name)
    if report.error_tasks or report.skipped_tasks or (status["exit_code"] and not report.failed_tasks):
        raise RuntimeError("MCPChecker reported errors or skipped tasks")
    report_dir = Path(workspace) / "reports" / submission
    report_dir.mkdir(parents=True, exist_ok=True)
    for name in ["report.json", "mcpchecker-report.json"]:
        (report_dir / name).write_text(report.model_dump_json(indent=2))
    handoff(
        workspace,
        uid,
        "mcpchecker",
        {
            "treatment-mean-reward": str(report.overall_score),
            "control-mean-reward": "0.0",
            "recommendation": report.recommendation,
            "results-dir": str(out_dir),
        },
    )


class Archive:
    """Upload and read back every required object before committing a manifest."""

    def __init__(self, prefix, bucket="ab-eval-reports"):
        from minio import Minio

        url = urlparse(os.environ["MINIO_ENDPOINT"])
        self.client = Minio(
            url.netloc or url.path,
            access_key=os.environ["MINIO_ACCESS_KEY"],
            secret_key=os.environ["MINIO_SECRET_KEY"],
            secure=url.scheme == "https",
        )
        self.bucket, self.prefix = bucket, prefix.rstrip("/")
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)
        self.entries = []
        from minio.error import S3Error

        try:
            response = self.client.get_object(bucket, self.prefix + "/archive-complete.json")
            try:
                self.entries = json.loads(response.read())["objects"]
            finally:
                response.close()
                response.release_conn()
        except S3Error as exc:
            if exc.code not in {"NoSuchKey", "NoSuchObject"}:
                raise

    def put(self, key, content):
        if isinstance(content, str):
            content = content.encode()
        name = self.prefix + "/" + key
        self.client.put_object(self.bucket, name, io.BytesIO(content), len(content))
        response = self.client.get_object(self.bucket, name)
        try:
            downloaded = response.read()
        finally:
            response.close()
            response.release_conn()
        digest = hashlib.sha256(content).hexdigest()
        if hashlib.sha256(downloaded).hexdigest() != digest:
            raise RuntimeError(f"archive read-back mismatch: {key}")
        self.entries = [entry for entry in self.entries if entry["key"] != name]
        self.entries.append({"key": name, "sha256": digest, "bytes": len(content)})

    def verified_cleanup(self, uid):
        """Resume after PVC release only from a fully verified committed archive."""
        from minio.error import S3Error

        def read(key):
            response = self.client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        try:
            manifest = json.loads(read(self.prefix + "/archive-complete.json"))
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                return False
            raise
        if manifest.get("uid") != uid or manifest.get("complete") is not True:
            raise ValueError("invalid committed archive identity")
        entries = manifest["objects"]
        if not any(entry["key"].endswith("/task-inventory.json") for entry in entries):
            return False
        for entry in entries:
            if not entry["key"].startswith(self.prefix + "/"):
                raise ValueError("archive entry is outside this run")
            content = read(entry["key"])
            if len(content) != entry["bytes"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise RuntimeError("committed archive read-back mismatch")
        cleanup = json.loads(read(self.prefix + "/cleanup.json"))
        return cleanup.get("uid") == uid and cleanup.get("deleted") is True

    def commit(self, uid):
        manifest = {
            "uid": uid,
            "complete": True,
            "objects": [entry for entry in self.entries if entry["key"] != self.prefix + "/archive-complete.json"],
        }
        self.put("archive-complete.json", json.dumps(manifest, indent=2))
        return manifest


def task_pods(cluster, uid, run_name):
    """Use TaskRun owner identity, never a reusable PipelineRun name alone."""
    pods = cluster.list("Pod", f"tekton.dev/pipelineRun={run_name},tekton.dev/memberOf=tasks")
    if uid == "legacy":
        return pods
    selected = []
    for pod in pods:
        owners = pod.metadata.owner_references or []
        for owner in owners:
            if owner.kind != "TaskRun":
                continue
            task = cluster.custom.get_namespaced_custom_object(
                "tekton.dev", "v1", cluster.namespace, "taskruns", owner.name
            )
            if task["metadata"]["uid"] == owner.uid and any(
                ref["uid"] == uid for ref in task["metadata"].get("ownerReferences", [])
            ):
                selected.append(pod)
    return selected


def require_task_evidence(cluster, uid, run_name, root):
    """Do not certify a complete archive after Tekton has removed a DAG Pod."""
    if uid == "legacy":
        return
    from kubernetes.client.exceptions import ApiException

    try:
        run = cluster.custom.get_namespaced_custom_object(
            "tekton.dev", "v1", cluster.namespace, "pipelineruns", run_name
        )
    except ApiException as exc:
        if exc.status != 404:
            raise
        raise RuntimeError("PipelineRun inventory unavailable; retain evidence for operator recovery") from exc
    if run["metadata"]["uid"] != uid:
        raise ValueError("PipelineRun identity changed during evidence collection")
    tasks = {
        task["metadata"]["name"]: task
        for task in cluster.custom.list_namespaced_custom_object("tekton.dev", "v1", cluster.namespace, "taskruns")[
            "items"
        ]
        if any(ref.get("uid") == uid for ref in task["metadata"].get("ownerReferences", []))
    }
    inventory = []
    missing = []
    for child in run.get("status", {}).get("childReferences", []):
        if child.get("kind") != "TaskRun":
            continue
        task = cluster.custom.get_namespaced_custom_object(
            "tekton.dev", "v1", cluster.namespace, "taskruns", child["name"]
        )
        tasks[child["name"]] = task
    for task_name, task in tasks.items():
        metadata = task["metadata"]
        if not any(ref.get("uid") == uid for ref in metadata.get("ownerReferences", [])):
            raise ValueError("TaskRun inventory owner mismatch")
        member = metadata.get("labels", {}).get("tekton.dev/memberOf")
        if member == "finally":
            continue
        if member != "tasks":
            raise ValueError("TaskRun inventory missing DAG/finally classification")
        status = task.get("status", {})
        attempts = [*status.get("retriesStatus", []), status]
        names = [attempt["podName"] for attempt in attempts if attempt.get("podName")]
        inventory.append(
            {
                "taskrun": task_name,
                "uid": metadata["uid"],
                "pods": names,
                "pod_identity_unknown": not names,
                "conditions": status.get("conditions", []),
            }
        )
        if any(not attempt.get("podName") for attempt in attempts):
            missing.append(task_name + ":Pod identity unavailable")
        for name in names:
            log = root / "pod-logs" / (name + ".log")
            snapshot = root / "pod-status" / (name + ".json")
            if not log.is_file() or not snapshot.is_file():
                missing.append(name)
                continue
            saved = json.loads(snapshot.read_text())
            if saved.get("taskrun_uid") != metadata["uid"] or saved.get("status", {}).get("phase") not in {
                "Succeeded",
                "Failed",
            }:
                missing.append(name)
    atomic_json(root / "task-inventory.json", {"uid": uid, "tasks": inventory, "missing": missing})
    if missing:
        raise RuntimeError("missing final DAG Pod evidence; retain resources: " + ", ".join(missing))


def quiesce(cluster, pods):
    """Stop producers without deleting their Pod log holders before archival."""
    stopped = []
    for pod in pods:
        phase = getattr(getattr(pod, "status", None), "phase", None)
        if phase and phase not in {"Succeeded", "Failed"}:
            cluster.core.patch_namespaced_pod(
                pod.metadata.name, cluster.namespace, {"spec": {"activeDeadlineSeconds": 1}}
            )
            deadline = time.monotonic() + 90
            while phase not in {"Succeeded", "Failed"}:
                if time.monotonic() >= deadline:
                    raise TimeoutError("evidence producer did not stop")
                time.sleep(2)
                pod = cluster.core.read_namespaced_pod(pod.metadata.name, cluster.namespace)
                phase = pod.status.phase
        stopped.append(pod)
    return stopped


def collect_logs(cluster, pods, root):
    from kubernetes.client.exceptions import ApiException

    for pod in pods:
        parts = []
        for container in pod.spec.containers:
            try:
                value = cluster.core.read_namespaced_pod_log(
                    pod.metadata.name, cluster.namespace, container=container.name, timestamps=True
                )
            except ApiException as exc:
                status = getattr(pod, "status", None)
                states = getattr(status, "container_statuses", None) or []
                state = next((item.state for item in states if item.name == container.name), None)
                never_started = (
                    exc.status == 400
                    and getattr(status, "phase", None) in {"Pending", "Failed"}
                    and (
                        state is None
                        or (
                            not state.running
                            and (state.waiting or (state.terminated and not state.terminated.started_at))
                        )
                    )
                )
                saved = root / "pod-logs" / f"{pod.metadata.name}.log"
                if exc.status == 404 and saved.exists():
                    value = saved.read_text()
                elif never_started:
                    value = "Container never started; no process log exists. See pod status."
                else:
                    raise
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="replace")
            parts.append(f"=== {container.name} ===\n{value}")
        log = root / "pod-logs" / f"{pod.metadata.name}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("\n".join(parts))
        status = getattr(pod, "status", None)
        atomic_json(
            root / "pod-status" / f"{pod.metadata.name}.json",
            {
                "name": pod.metadata.name,
                "pod_uid": getattr(pod.metadata, "uid", None),
                "labels": getattr(pod.metadata, "labels", None) or {},
                "taskrun_uid": next(
                    (
                        ref.uid
                        for ref in (getattr(pod.metadata, "owner_references", None) or [])
                        if ref.kind == "TaskRun"
                    ),
                    None,
                ),
                "images": [c.image for c in pod.spec.containers],
                "status": status.to_dict() if status else {},
            },
        )


def finalize(cluster, workspace, uid, run_name, submission="", archive_factory=Archive):
    root = paths(workspace, uid)
    selector = f"{LABEL}={uid}"
    # Stop provisioning/evaluation before discovering the final mock set.
    pods = quiesce(cluster, task_pods(cluster, uid, run_name))
    mocks = quiesce(cluster, cluster.list("Pod", selector))
    # Save logs/status locally before contacting storage. Missing logs on a
    # never-started container are evidence of setup failure, not an archive error.
    collect_logs(cluster, [*mocks, *pods], root)
    require_task_evidence(cluster, uid, run_name, root)
    archive = archive_factory(f"mcp-runs/{uid}")
    prefix = f"mcp-runs/{uid}"
    for log in sorted((root / "pod-logs").glob("*.log")):
        archive.put(f"pod-logs/{log.name}", log.read_bytes())
    # Only controlled evidence paths, never the source checkout or resolved credentials.
    context_file = root / "context.json"
    context = json.loads(context_file.read_text()) if context_file.exists() else {}
    submission = context.get("submission", submission)
    if submission and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", submission):
        raise ValueError("invalid submission name in context")
    required = []
    complete = (root / "result.json").exists()
    header = json.loads((root / "result.json").read_text()) if complete else {}
    if complete and header.get("engine", "mcpchecker") == "mcpchecker":
        if not submission:
            raise ValueError("completed evaluation is missing its submission context")
        required = [
            Path(workspace) / "eval-results" / submission / name
            for name in ["mcpchecker-process.log", "process-status.json", "mcpchecker-out.json"]
        ] + [Path(workspace) / "reports" / submission / "mcpchecker-report.json"]
        if (root / "target.json").exists():
            target = json.loads((root / "target.json").read_text())
            required += [
                root / "readiness.json",
                root / "target.json",
                root / "pod-logs" / (target["name"] + ".log"),
                root / "pod-status" / (target["name"] + ".json"),
            ]
        if not any((root / "pod-status").glob("*.json")):
            raise RuntimeError("completed evaluation lacks preserved Pod identity/status evidence")
        if any(not path.is_file() for path in required):
            raise RuntimeError("completed evaluation is missing required raw evidence")
    if complete and header.get("engine") == "harbor":
        if not submission:
            raise ValueError("completed Harbor evaluation lacks submission context")
        required = [
            Path(workspace) / "reports" / submission / "report.json",
            Path(workspace) / "eval-results" / submission / "harbor-pairs.json",
        ]
        if any(not path.is_file() for path in required):
            raise RuntimeError("completed Harbor evaluation lacks raw pair evidence")
        from scripts.run_harbor_mcp import verify_pair_archives

        verify_pair_archives(workspace, uid, submission)
    atomic_json(
        root / "lifecycle.json",
        {
            "uid": uid,
            "run_name": run_name,
            "outcome": "evaluation-complete" if complete else "infrastructure-incomplete",
            "required": [str(path.relative_to(workspace)) for path in required],
        },
    )
    selected = [root]
    if submission:
        selected += [Path(workspace) / "eval-results" / submission, Path(workspace) / "reports" / submission]
    for dirname in selected:
        if dirname.is_dir():
            for path in sorted(dirname.rglob("*")):
                if (
                    path.is_file()
                    and path.suffix in {".json", ".jsonl", ".log", ".txt", ".md"}
                    and path.name != "mcp-config.yaml"
                ):
                    archive.put(f"workspace/{path.relative_to(workspace)}", path.read_bytes())
    # If normal store was skipped after evaluation, persist any valid reports idempotently.
    reports = Path(workspace) / "reports"
    if required and not os.environ.get("DATABASE_URL"):
        raise RuntimeError("completed MCP evaluation requires PostgreSQL persistence")
    if os.environ.get("DATABASE_URL") and reports.is_dir():
        from scripts.store_results import store

        report_name = "report.json" if header.get("engine") == "harbor" else "mcpchecker-report.json"
        for report in [reports / submission / report_name] if submission else []:
            if not report.exists():
                continue
            if not store(report.parent, run_id=uid):
                raise RuntimeError("PostgreSQL persistence failed")
    resources = {kind: cluster.list(kind, selector) for kind in ["Pod", "Service", "NetworkPolicy"]}
    archive.put(
        "identity.json",
        json.dumps(
            {
                "uid": uid,
                "run_name": run_name,
                "resources": {kind: [x.metadata.name for x in items] for kind, items in resources.items()},
            }
        ),
    )
    archive.commit(uid)
    # No ownerRef permits PipelineRun pruning to bypass the evidence gate.
    for kind in ["Pod", "Service", "NetworkPolicy"]:
        for obj in resources[kind]:
            cluster.delete(kind, obj.metadata.name)
    deadline = time.monotonic() + 90
    while any(cluster.list(kind, selector) for kind in resources):
        if time.monotonic() >= deadline:
            raise TimeoutError("managed resources still present after delete")
        time.sleep(2)
    archive.put("cleanup.json", json.dumps({"uid": uid, "deleted": True}))
    return prefix


def workspaces(cluster, uid):
    return [
        pvc
        for pvc in cluster.core.list_namespaced_persistent_volume_claim(cluster.namespace).items
        if (pvc.metadata.labels or {}).get(LABEL) == uid
        or any(owner.uid == uid for owner in (pvc.metadata.owner_references or []))
    ]


def release_workspace(cluster, uid, run_name):
    """Used by orphan recovery after the same archive gate as normal finalization."""
    from kubernetes.client.exceptions import ApiException

    for pod in task_pods(cluster, uid, run_name):
        cluster.delete("Pod", pod.metadata.name)
    for obj in cluster.apps.list_namespaced_stateful_set(cluster.namespace).items:
        if any(x.uid == uid for x in (obj.metadata.owner_references or [])):
            cluster.apps.delete_namespaced_stateful_set(obj.metadata.name, cluster.namespace)
    claims = workspaces(cluster, uid)
    for pvc in claims:
        try:
            cluster.core.delete_namespaced_persistent_volume_claim(pvc.metadata.name, cluster.namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise
    deadline = time.monotonic() + 300
    while workspaces(cluster, uid):
        if time.monotonic() >= deadline:
            raise TimeoutError("evidence PVC still present after recovery cleanup")
        time.sleep(2)
    protect_run(cluster, uid, run_name, False)
    return bool(claims)


def archive_job(cluster, uid, run_name, image, pvc, node, name):
    """A temporary worker owns the PVC mount; the finalizer never mounts it."""
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "labels": {LABEL: uid}},
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": 300,
            "template": {
                "metadata": {"labels": {"abevalflow.io/mcp-archive": uid}},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": "mcp-lifecycle",
                    "nodeSelector": {"kubernetes.io/hostname": node},
                    "containers": [
                        {
                            "name": "archive",
                            "image": image,
                            "env": storage_env() + [{"name": "POD_NAMESPACE", "value": cluster.namespace}],
                            "command": [
                                "python",
                                "-m",
                                "abevalflow.mcp_ci",
                                "finalize",
                                "--uid",
                                uid,
                                "--run-name",
                                run_name,
                            ],
                            "resources": {
                                "requests": {"cpu": "50m", "memory": "128Mi"},
                                "limits": {"cpu": "500m", "memory": "512Mi"},
                            },
                            "volumeMounts": [{"name": "source", "mountPath": "/workspace/source"}],
                        }
                    ],
                    "volumes": [{"name": "source", "persistentVolumeClaim": {"claimName": pvc}}],
                },
            },
        },
    }


def finish(cluster, uid, run_name, image):
    from kubernetes import client
    from kubernetes.client.exceptions import ApiException

    if uid == "legacy":
        # Existing custom pipelines use the original pipeline SA, which has
        # Pod/log access but no managed lifecycle privileges.
        root = Path("/tmp/legacy-logs")
        collect_logs(cluster, task_pods(cluster, uid, run_name), root)
        archive = Archive(f"pod-logs/{run_name}")
        for log in sorted((root / "pod-logs").glob("*.log")):
            archive.put(log.name, log.read_bytes())
        archive.commit(run_name)
        return
    pvcs = workspaces(cluster, uid)
    if not pvcs:
        if Archive(f"mcp-runs/{uid}").verified_cleanup(uid):
            return
        # Legacy runs / failures before MCP setup still need their DAG logs archived.
        finalize(cluster, "/tmp/no-workspace", uid, run_name)
        return
    if len(pvcs) != 1:
        raise ValueError("ambiguous evidence PVC")
    pvc = pvcs[0]
    nodes = {
        pod.spec.node_name
        for pod in cluster.list("Pod", f"tekton.dev/pipelineRun={run_name}")
        if pod.spec.node_name
        and any(
            v.persistent_volume_claim and v.persistent_volume_claim.claim_name == pvc.metadata.name
            for v in pod.spec.volumes
        )
    }
    node = (pvc.metadata.annotations or {}).get("abevalflow.io/evidence-node")
    if not node and len(nodes) == 1:
        node = nodes.pop()
    if not node:
        raise ValueError("cannot determine evidence PVC node")
    cluster.core.patch_namespaced_persistent_volume_claim(
        pvc.metadata.name,
        cluster.namespace,
        {
            "metadata": {
                "labels": {LABEL: uid},
                "annotations": {"abevalflow.io/pipeline-run": run_name, "abevalflow.io/evidence-node": node},
                "ownerReferences": [],
            }
        },
    )
    name = "mcp-archive-" + hashlib.sha256(uid.encode()).hexdigest()[:16]
    batch = client.BatchV1Api()
    try:
        batch.create_namespaced_job(
            cluster.namespace, archive_job(cluster, uid, run_name, image, pvc.metadata.name, node, name)
        )
    except ApiException as exc:
        if exc.status != 409:
            raise
        previous = batch.read_namespaced_job(name, cluster.namespace)
        if previous.status.failed:
            # Keep the evidence PVC and mock; only the failed archive worker is
            # disposable. Its deterministic replacement retries the same keys.
            batch.delete_namespaced_job(name, cluster.namespace, propagation_policy="Foreground")
            stop = time.monotonic() + 60
            while True:
                try:
                    batch.read_namespaced_job(name, cluster.namespace)
                except ApiException as gone:
                    if gone.status == 404:
                        break
                    raise
                if time.monotonic() >= stop:
                    raise TimeoutError("failed archive worker did not disappear")
                time.sleep(2)
            batch.create_namespaced_job(
                cluster.namespace, archive_job(cluster, uid, run_name, image, pvc.metadata.name, node, name)
            )
    deadline = time.monotonic() + 330
    while time.monotonic() < deadline:
        job = batch.read_namespaced_job(name, cluster.namespace)
        if job.status.failed:
            raise RuntimeError("evidence archive worker failed; keeping evidence PVC and mock resources")
        if job.status.succeeded:
            batch.delete_namespaced_job(name, cluster.namespace, propagation_policy="Foreground")
            stop = time.monotonic() + 60
            while cluster.list("Pod", f"job-name={name}"):
                if time.monotonic() >= stop:
                    raise TimeoutError("archive worker still mounts evidence PVC")
                time.sleep(2)
            return
        time.sleep(2)
    raise TimeoutError("evidence archive worker did not finish")


def sweep(cluster, image):
    """Retry archive-gated cleanup even after PipelineRun deletion or cancellation."""
    from kubernetes.client.exceptions import ApiException

    pvcs = cluster.core.list_namespaced_persistent_volume_claim(cluster.namespace, label_selector=LABEL).items
    candidates = {
        (pvc.metadata.labels or {}).get(LABEL, "unknown"): (pvc.metadata.annotations or {}).get(
            "abevalflow.io/pipeline-run"
        )
        for pvc in pvcs
    }
    # Protection is applied first; a crash before pinning is still discoverable.
    runs = cluster.custom.list_namespaced_custom_object("tekton.dev", "v1", cluster.namespace, "pipelineruns")["items"]
    for run in runs:
        if PROTECTION in run["metadata"].get("finalizers", []):
            candidates[run["metadata"]["uid"]] = run["metadata"]["name"]
    failures = []
    for uid, run_name in candidates.items():
        try:
            if not run_name:
                raise ValueError("evidence PVC lacks its run identity")
            try:
                run = cluster.custom.get_namespaced_custom_object(
                    "tekton.dev", "v1", cluster.namespace, "pipelineruns", run_name
                )
                if (
                    run["metadata"]["uid"] == uid
                    and not run.get("status", {}).get("completionTime")
                    and not run["metadata"].get("deletionTimestamp")
                ):
                    continue
            except ApiException as exc:
                if exc.status != 404:
                    raise
            finish(cluster, uid, run_name, image)
            release_workspace(cluster, uid, run_name)
        except Exception as exc:
            failures.append(f"{uid}: {type(exc).__name__}")
    if failures:
        raise RuntimeError("Orphan recovery failed for: " + "; ".join(failures))


def storage_env():
    values = [
        ("MINIO_ENDPOINT", "minio-credentials", "endpoint-url"),
        ("MINIO_ACCESS_KEY", "minio-credentials", "root-user"),
        ("MINIO_SECRET_KEY", "minio-credentials", "root-password"),
        ("DATABASE_URL", "ab-eval-db-credentials", "database-url"),
    ]
    return [
        {"name": name, "valueFrom": {"secretKeyRef": {"name": secret, "key": key, "optional": name == "DATABASE_URL"}}}
        for name, secret, key in values
    ]


def duration_seconds(value):
    if str(value).isdigit():
        return int(value)
    parts = re.findall(r"(\d+)([smh])", str(value))
    if not parts or "".join(a + b for a, b in parts) != value:
        raise ValueError("invalid duration")
    return sum(int(n) * {"s": 1, "m": 60, "h": 3600}[unit] for n, unit in parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["setup", "evaluate", "bridge", "finalize", "recover", "sweep", "finish", "release"]
    )
    parser.add_argument("--workspace", default="/workspace/source")
    parser.add_argument("--uid", default="sweep")
    parser.add_argument("--run-name", default="sweep")
    parser.add_argument("--image", default="")
    parser.add_argument("--namespace", default=os.environ.get("POD_NAMESPACE", "ab-eval-flow"))
    parser.add_argument("--submission-dir", default="")
    parser.add_argument("--submission", default="")
    parser.add_argument("--attempt", default=os.environ.get("POD_UID", "initial"))
    parser.add_argument("--mock", default="")
    parser.add_argument("--engine", default="mcpchecker")
    parser.add_argument("--agent-model", default="openai:claude-sonnet")
    parser.add_argument("--judge-model", default="openai:claude-sonnet")
    parser.add_argument("--timeout", type=duration_seconds, default=600)
    parser.add_argument("--result-dir", default="/tekton/results")
    args = parser.parse_args()
    if args.action == "release":
        deleted = release_workspace(Cluster(args.namespace), args.uid, args.run_name)
        (Path(args.result_dir) / "cleanup-status").write_text("deleted" if deleted else "none")
        return
    if args.action == "finish":
        finish(Cluster(args.namespace), args.uid, args.run_name, args.image)
        return
    if args.action == "sweep":
        sweep(Cluster(args.namespace), args.image)
        return
    if args.action == "setup":
        cluster = Cluster(args.namespace)
        protect_run(cluster, args.uid, args.run_name, True)
        pin_workspace(cluster, args.workspace, args.uid, args.run_name)
        source = submission_path(args.workspace, args.submission_dir)
        inputs = [
            path
            for path in source.rglob("*")
            if path.is_file()
            and not path.is_symlink()
            and path.resolve().is_relative_to(source)
            and path.name != "mcp-config.yaml"
            and path.suffix in {".yaml", ".yml"}
        ]
        atomic_json(
            paths(args.workspace, args.uid) / "context.json",
            {
                "submission": args.submission,
                "uid": args.uid,
                "run_name": args.run_name,
                "runtime_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "input_sha256": {
                    str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs
                },
            },
        )
        metadata = yaml.safe_load((submission_path(args.workspace, args.submission_dir) / "metadata.yaml").read_text())
        mock = (metadata.get("mcp") or {}).get("mock")
        if mock:
            setup(cluster, args.workspace, args.uid, args.attempt, args.run_name, mock)
    elif args.action == "evaluate":
        evaluate(
            args.workspace,
            args.uid,
            args.run_name,
            args.submission_dir,
            args.submission,
            args.agent_model,
            args.judge_model,
            args.timeout,
        )
    elif args.action == "bridge":
        bridge(args.workspace, args.uid, args.engine, args.result_dir)
        context = paths(args.workspace, args.uid) / "context.json"
        if not context.exists():
            atomic_json(
                context,
                {"uid": args.uid, "submission": args.submission, "run_name": args.run_name, "engine": args.engine},
            )
    else:
        cluster = Cluster(args.namespace)
        finalize(cluster, args.workspace, args.uid, args.run_name, args.submission)
        if args.action == "recover":
            release_workspace(cluster, args.uid, args.run_name)


if __name__ == "__main__":
    main()
