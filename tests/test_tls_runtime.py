"""The opt-in legacy TLS mode reaches agents, judges, and external Python only."""

import json
import os
import ssl
import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from abevalflow.config.runtime import resolve_config
from abevalflow.execution.cluster import runner_environment, trial_environment
from abevalflow.harbor_extensions.aeh_task_enrichment import enrich_harbor_tasks
from abevalflow.review.service import ReviewService


@pytest.mark.parametrize("insecure", [False, True])
def test_transport_mode_is_explicit_and_never_copies_keys_into_tasks(catalog, insecure):
    config = resolve_config({**catalog, "insecure_ssl": insecure, "judge_base_url": "https://judge/v1"}, {})
    env = runner_environment(config, config.output / "reports")
    env["OPENAI_API_KEY"] = "private-key"
    pod_env = trial_environment(env)
    assert pod_env["OPENAI_BASE_URL"] == "https://judge/v1"
    assert "OPENAI_API_KEY" not in pod_env
    assert "private-key" not in json.dumps(pod_env)
    assert (pod_env.get("NODE_TLS_REJECT_UNAUTHORIZED") == "0") is insecure
    assert env["AEH_INSECURE_SSL"] == str(int(insecure))


def test_legacy_tls_alias_and_explicit_secure_override(catalog):
    config = resolve_config(catalog, {"AEH_INSECURE_SSL": "1"})
    assert config.insecure_ssl
    config = resolve_config({**catalog, "insecure_ssl": False}, {"AEH_INSECURE_SSL": "1"})
    with patch.dict(os.environ, {"AEH_INSECURE_SSL": "1"}):
        env = runner_environment(config, config.output / "reports")
    assert env["AEH_INSECURE_SSL"] == "0"
    assert "AEH_INSECURE_SSL" not in trial_environment(env)


def test_native_task_environment_survives_enrichment_without_changing_verifier(tmp_path):
    submission = tmp_path / "submission"
    submission.mkdir()
    config = submission / "eval.yaml"
    config.write_text("dataset: {path: cases}\njudges: []\n")
    task = tmp_path / "tasks/case-001"
    (task / "environment").mkdir(parents=True)
    (task / "tests").mkdir()
    (task / "task.toml").write_text('[environment]\ndocker_image="trial"\n[environment.env]\nEXISTING="yes"\n')
    verifier = task / "tests/test.sh"
    verifier.write_text("python3 -m agent_eval.harbor.reward\n")
    enrich_harbor_tasks(
        task.parent,
        config_path=config,
        runtime_env={"AEH_INSECURE_SSL": "1", "NODE_TLS_REJECT_UNAUTHORIZED": "0"},
    )
    env = tomllib.loads((task / "task.toml").read_text())["environment"]["env"]
    assert env == {"EXISTING": "yes", "AEH_INSECURE_SSL": "1", "NODE_TLS_REJECT_UNAUTHORIZED": "0"}
    assert verifier.read_text() == "python3 -m agent_eval.harbor.reward\n"


@pytest.mark.parametrize("insecure", [False, True])
def test_python_hook_is_opt_in_and_does_not_disable_kubernetes_tls(insecure):
    hook = Path(__file__).parents[1] / "abevalflow/runtime_hooks"
    probe = (
        "import json,ssl,httpx; "
        "c=httpx.Client(); "
        "print(json.dumps([c._transport._pool._ssl_context.verify_mode,ssl.create_default_context().verify_mode])); "
        "c.close()"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        env={**os.environ, "PYTHONPATH": str(hook), "AEH_INSECURE_SSL": str(int(insecure))},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(result.stdout) == [ssl.CERT_NONE if insecure else ssl.CERT_REQUIRED, ssl.CERT_REQUIRED]


@pytest.mark.parametrize("insecure", [False, True])
def test_review_client_uses_the_same_tls_mode(catalog, insecure):
    config = resolve_config(
        {
            **catalog,
            "insecure_ssl": insecure,
            "llm_base_url": "https://review/v1",
            "llm_model": "model",
            "llm_api_key": "private-key",
        },
        {},
    )
    client = MagicMock()
    with patch("openai.DefaultHttpxClient", return_value=client) as transport, patch("openai.OpenAI") as sdk:
        ReviewService(config)
    transport.assert_called_once_with(verify=not insecure)
    assert sdk.call_args.kwargs["http_client"] is client
