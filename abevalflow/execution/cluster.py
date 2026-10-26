"""Explicit local-orchestrator configuration for remote Kubernetes trial pods."""

from __future__ import annotations

import os
from pathlib import Path

from abevalflow.config.runtime import RuntimeConfig


def runner_environment(config: RuntimeConfig, reports: Path) -> dict[str, str]:
    """Build a child environment; explicit kubeconfig takes precedence over in-cluster discovery."""
    environment = os.environ.copy()
    if config.kubeconfig:
        environment["KUBECONFIG"] = str(config.kubeconfig)
        environment.pop("KUBERNETES_SERVICE_HOST", None)
        environment.pop("KUBERNETES_SERVICE_PORT", None)
        if config.kube_context:
            import yaml

            raw = yaml.safe_load(config.kubeconfig.read_text())
            if config.kube_context not in {c["name"] for c in raw.get("contexts", [])}:
                raise ValueError(f"Kube context not found: {config.kube_context}")
            raw["current-context"] = config.kube_context
            for cluster in raw.get("clusters", []):
                value = cluster.get("cluster", {})
                if value.get("certificate-authority"):
                    value["certificate-authority"] = str(
                        (config.kubeconfig.parent / value["certificate-authority"]).resolve()
                    )
            for user in raw.get("users", []):
                value = user.get("user", {})
                for key in ("client-certificate", "client-key"):
                    if value.get(key):
                        value[key] = str((config.kubeconfig.parent / value[key]).resolve())
            selected = config.output / "kubeconfig"
            selected.write_text(yaml.safe_dump(raw))
            selected.chmod(0o600)
            environment["KUBECONFIG"] = str(selected)
    environment.update(
        AGENT_EVAL_K8S_NAMESPACE=config.namespace,
        AGENT_EVAL_K8S_CREDENTIALS_SECRET=config.credentials_secret,
        AGENT_EVAL_RUNS_DIR=str(reports),
        AGENT_EVAL_K8S_KEEP_RUN="0",
    )
    if config.agent_base_url:
        environment["ANTHROPIC_BASE_URL"] = config.agent_base_url
    if config.agent_api_key:
        environment["ANTHROPIC_API_KEY"] = config.agent_api_key.get_secret_value()
    if config.judge_base_url:
        environment["OPENAI_BASE_URL"] = config.judge_base_url
    if config.judge_api_key:
        environment["OPENAI_API_KEY"] = config.judge_api_key.get_secret_value()
    if config.score_script:
        environment["AEH_SCORE_PY"] = str(config.score_script)
    return environment
