"""Timeout recovery for the pinned AEH Kubernetes and Harbor trial-name contracts."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path


class CleanupError(RuntimeError):
    """Workload cleanup could not be confirmed; further trials must not start."""


def trial_pod_names(jobs: Path) -> set[str]:
    """Harbor persists trial config before start; AEH names pods from <trial>__env."""
    names = set()
    for path in jobs.glob("*/*/config.json"):
        raw = json.loads(path.read_text())
        name = raw.get("trial_name")
        if not isinstance(name, str) or name != path.parent.name:
            raise CleanupError("Invalid Harbor trial ownership evidence")
        session = re.sub(r"[^a-z0-9-]", "-", f"{name}__env".lower())
        session = re.sub(r"-+", "-", session).strip("-")
        names.add(f"aeh-{session}"[:62].strip("-"))
    return names


def cleanup_trials(jobs: Path, environment: dict[str, str] | None, *, timeout: float = 60) -> None:
    """Delete only this invocation's recorded pods, with UID preconditions, and await absence."""
    try:
        names = trial_pod_names(jobs)
        if not names:
            return  # No trial config means no trial was started.
        from kubernetes import client
        from kubernetes import config as kube
        from kubernetes.client.rest import ApiException

        env = environment if environment is not None else os.environ
        if env.get("KUBECONFIG"):
            api = kube.new_client_from_config(env["KUBECONFIG"])
        else:
            try:
                kube.load_incluster_config()
            except kube.ConfigException:
                kube.load_kube_config()
            api = client.ApiClient()
        namespace = env.get("AGENT_EVAL_K8S_NAMESPACE", "default")
        with api:
            core = client.CoreV1Api(api)
            for name in sorted(names):
                try:
                    pod = core.read_namespaced_pod(name, namespace, _request_timeout=10)
                except ApiException as exc:
                    if exc.status == 404:
                        continue
                    raise
                if (pod.metadata.labels or {}).get("app.kubernetes.io/managed-by") != "agent-eval-harness":
                    raise CleanupError("Pod ownership does not match recorded Harbor trial")
                uid = pod.metadata.uid
                if not uid:
                    raise CleanupError("Recorded trial pod has no UID")
                try:
                    core.delete_namespaced_pod(
                        name,
                        namespace,
                        grace_period_seconds=0,
                        body=client.V1DeleteOptions(preconditions=client.V1Preconditions(uid=uid)),
                        _request_timeout=10,
                    )
                except ApiException as exc:
                    if exc.status != 404:
                        raise
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        current = core.read_namespaced_pod(name, namespace, _request_timeout=10)
                        if current.metadata.uid != uid:
                            raise CleanupError("Trial pod was replaced during cleanup")
                    except ApiException as exc:
                        if exc.status == 404:
                            break
                        raise
                    if time.monotonic() >= deadline:
                        raise CleanupError("Timed out waiting for trial pod cleanup")
                    time.sleep(0.5)
    except CleanupError:
        raise
    except Exception as exc:
        # SDK errors can include credentials; keep them out of stored evidence.
        raise CleanupError(f"Trial cleanup failed ({type(exc).__name__})") from exc
