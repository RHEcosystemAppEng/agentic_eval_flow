"""Local orchestration, remote-cluster configuration, and dependency readiness."""

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from abevalflow.config.runtime import resolve_config
from abevalflow.execution.cluster import runner_environment
from abevalflow.workflow.readiness import check_readiness


def test_explicit_kubeconfig_overrides_incluster_and_keeps_credentials_private(catalog, tmp_path, monkeypatch):
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text(
        yaml.safe_dump(
            {
                "current-context": "old",
                "contexts": [{"name": "remote", "context": {"namespace": "trials"}}],
                "clusters": [{"name": "remote", "cluster": {"certificate-authority": "ca.pem"}}],
                "users": [{"name": "remote", "user": {"client-key": "key.pem", "client-certificate": "cert.pem"}}],
            }
        )
    )
    config = resolve_config(
        {
            **catalog,
            "kubeconfig": kubeconfig,
            "kube_context": "remote",
            "agent_base_url": "https://agents.example",
            "agent_api_key": "agent-secret",
            "judge_base_url": "https://judges.example",
            "judge_api_key": "judge-secret",
        },
        {},
    )
    config.output.mkdir()
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "incluster")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", "443")
    env = runner_environment(config, config.output / "reports")
    assert "KUBERNETES_SERVICE_HOST" not in env
    assert env["AGENT_EVAL_K8S_NAMESPACE"] == "trials"
    assert env["ANTHROPIC_API_KEY"] == "agent-secret"
    assert env["OPENAI_API_KEY"] == "judge-secret"
    staged = Path(env["KUBECONFIG"])
    assert staged.stat().st_mode & 0o777 == 0o600
    raw = yaml.safe_load(staged.read_text())
    assert raw["current-context"] == "remote"
    assert raw["users"][0]["user"]["client-key"] == str(tmp_path / "key.pem")
    assert raw["clusters"][0]["cluster"]["certificate-authority"] == str(tmp_path / "ca.pem")
    assert "agent-secret" not in config.model_dump_json()


def test_unknown_context_rejected(catalog, tmp_path):
    kube = tmp_path / "config"
    kube.write_text("contexts: []\n")
    config = resolve_config({**catalog, "kubeconfig": kube, "kube_context": "missing"}, {})
    with pytest.raises(ValueError, match="context not found"):
        runner_environment(config, config.output / "reports")


def test_doctor_offline_checks_requested_capabilities_only(catalog):
    config = resolve_config(catalog, {})
    with patch("shutil.which", return_value=None):
        checks = check_readiness(config, cluster=False)
    assert {c["name"] for c in checks} == {"skill-scanner", "harness-eval"}
    assert all(not c["ready"] for c in checks)


def test_container_scanners_do_not_require_host_python_scanner_packages(catalog):
    config = resolve_config({**catalog, "scanner_runtime": "podman"}, {})
    with patch("shutil.which", return_value="/bin/podman"):
        checks = check_readiness(config, cluster=False)
    assert checks == [{"name": "podman", "ready": True, "detail": "Scanner executable must be on PATH"}]


def test_doctor_reports_model_and_storage_configuration(catalog):
    config = resolve_config({**catalog, "tiers": "2", "persist": "postgres,s3,compass"}, {})
    with patch("importlib.util.find_spec", return_value=None):
        checks = check_readiness(config, cluster=False)
    by_name = {c["name"]: c for c in checks}
    assert not by_name["aeh-runtime"]["ready"]
    assert not by_name["review-endpoint"]["ready"]
    assert not by_name["postgres-config"]["ready"]
    assert not by_name["s3-config"]["ready"]
    assert not by_name["compass-config"]["ready"]
    assert not by_name["pairwise-scorer"]["ready"]


def test_cluster_doctor_checks_permissions_and_secret_without_creating_pods(catalog, tmp_path, monkeypatch):
    kube = tmp_path / "config"
    kube.write_text("contexts: []\n")
    config = resolve_config({**catalog, "tiers": "2", "kubeconfig": kube}, {})
    client = MagicMock()
    client.AuthorizationV1Api.return_value.create_self_subject_access_review.return_value.status.allowed = True
    kconfig = MagicMock()
    module = ModuleType("kubernetes")
    module.client = client
    module.config = kconfig
    monkeypatch.setitem(sys.modules, "kubernetes", module)
    with patch("importlib.util.find_spec", return_value=SimpleNamespace(origin="dependency")):
        checks = check_readiness(config)
    assert any(c["name"] == "workload-secret" and c["ready"] for c in checks)
    client.CoreV1Api.return_value.create_namespaced_pod.assert_not_called()
    kconfig.new_client_from_config.assert_called_once()


def test_cluster_failure_does_not_expose_sdk_credentials(catalog, tmp_path, monkeypatch):
    kube = tmp_path / "config"
    kube.write_text("contexts: []\n")
    config = resolve_config({**catalog, "tiers": "2", "kubeconfig": kube}, {})
    module = ModuleType("kubernetes")
    module.client = MagicMock()
    module.config = MagicMock()
    module.config.new_client_from_config.side_effect = RuntimeError("private-token")
    monkeypatch.setitem(sys.modules, "kubernetes", module)
    with patch("importlib.util.find_spec", return_value=SimpleNamespace(origin="dependency")):
        checks = check_readiness(config)
    assert "private-token" not in json.dumps(checks)
    assert any(c["name"] == "cluster" and not c["ready"] for c in checks)


def test_scanner_container_paths_are_translated(catalog):
    from abevalflow.validation.scanners import scanner_command

    config = resolve_config({**catalog, "scanner_runtime": "docker"}, {})
    command = scanner_command(config, "security", ["skill-scanner", "scan", str(config.output / "source/skill")])
    assert command[0] == "docker"
    assert "--network=none" in command
    assert command[-1] == "/source/skill"
    assert config.security_image in command
