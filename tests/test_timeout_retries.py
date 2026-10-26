"""Bounded timeout retries, process reaping, and scoped remote cleanup."""

import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from abevalflow.execution.cleanup import CleanupError, cleanup_trials, trial_pod_names
from abevalflow.execution.harbor import EvaluationTimeout, HarborRunner
from abevalflow.execution.process import run_process


def trial(jobs, exception=None):
    path = jobs / "job" / "case__abc"
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text(json.dumps({"trial_name": path.name}))
    (path / "result.json").write_text(json.dumps({"exception_info": {"exception_type": exception}}))
    return path


@pytest.mark.parametrize("native", [False, True])
def test_retry_waits_for_cleanup_and_archives_partial_evidence(tmp_path, native):
    output, jobs = tmp_path / "output", tmp_path / "jobs"
    output.mkdir()
    (output / "partial.txt").write_text("attempt 1")
    events = []
    runtime = MagicMock()

    def execute(*args, **kwargs):
        events.append("execute")
        if events.count("execute") == 1:
            trial(jobs, "AgentTimeoutError" if native else None)
            if not native:
                raise subprocess.TimeoutExpired("abeval", 1)
            return 1
        assert (tmp_path / "output-timeouts/attempt-1/output/partial.txt").is_file()
        assert not (output / "partial.txt").exists()
        return 0

    runtime.run.side_effect = execute
    with patch("abevalflow.execution.harbor.cleanup_trials", side_effect=lambda *args: events.append("cleanup")):
        code = HarborRunner(model="m", timeout_retries=1)._run_with_timeout_retries(
            runtime, ["native"], tmp_path / "eval.yaml", output, jobs
        )
    assert code == 0
    assert events == ["execute", "cleanup", "execute"]
    history = json.loads((tmp_path / "output-attempts.json").read_text())
    assert history[0]["cleanup_completed"] and len(history) == 2


@pytest.mark.parametrize("retries", [0, 2])
def test_timeout_retry_budget_is_bounded(tmp_path, retries):
    runtime = MagicMock()
    runtime.run.side_effect = subprocess.TimeoutExpired("native", 1)
    with patch("abevalflow.execution.harbor.cleanup_trials") as cleanup, pytest.raises(EvaluationTimeout):
        HarborRunner(model="m", timeout_retries=retries)._run_with_timeout_retries(
            runtime, [], tmp_path / "eval.yaml", tmp_path / "output", tmp_path / "jobs"
        )
    assert runtime.run.call_count == retries + 1
    assert cleanup.call_count == retries + 1


@pytest.mark.parametrize("code", [0, 1, 3])
def test_non_timeout_exit_never_retries(tmp_path, code):
    runtime = MagicMock()
    runtime.run.return_value = code
    with patch("abevalflow.execution.harbor.cleanup_trials"):
        assert (
            HarborRunner(model="m", timeout_retries=5)._run_with_timeout_retries(
                runtime, [], tmp_path / "eval.yaml", tmp_path / "output", tmp_path / "jobs"
            )
            == code
        )
    runtime.run.assert_called_once()


def test_cleanup_failure_prevents_retry(tmp_path):
    runtime = MagicMock()
    runtime.run.side_effect = subprocess.TimeoutExpired("native", 1)
    with patch("abevalflow.execution.harbor.cleanup_trials", side_effect=CleanupError("unconfirmed")):
        with pytest.raises(CleanupError):
            HarborRunner(model="m")._run_with_timeout_retries(
                runtime, [], tmp_path / "eval.yaml", tmp_path / "output", tmp_path / "jobs"
            )
    runtime.run.assert_called_once()


def test_process_timeout_kills_descendants_even_when_parent_exits(tmp_path):
    import time

    heartbeat = tmp_path / "heartbeat"
    child = (
        "import signal,time,pathlib; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"p=pathlib.Path({str(heartbeat)!r}); "
        "exec('while True: p.write_text(str(time.time_ns())); time.sleep(0.01)')"
    )
    program = f"import subprocess,time,sys; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(120)"
    with pytest.raises(subprocess.TimeoutExpired):
        run_process([sys.executable, "-c", program], timeout=0.5, grace=0.2)
    stopped = heartbeat.read_text()
    time.sleep(0.1)
    assert heartbeat.read_text() == stopped


def test_remote_cleanup_uses_only_recorded_names_and_uid_preconditions(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    trial(jobs)
    assert trial_pod_names(jobs) == {"aeh-case-abc-env"}

    class ApiException(Exception):
        def __init__(self, status):
            self.status = status

    core = MagicMock()
    pod = SimpleNamespace(
        metadata=SimpleNamespace(uid="owned-uid", labels={"app.kubernetes.io/managed-by": "agent-eval-harness"})
    )
    core.read_namespaced_pod.side_effect = [pod, ApiException(404)]
    client = MagicMock()
    client.CoreV1Api.return_value = core
    kube = MagicMock()
    monkeypatch.setitem(sys.modules, "kubernetes", SimpleNamespace(client=client, config=kube))
    monkeypatch.setitem(sys.modules, "kubernetes.client.rest", SimpleNamespace(ApiException=ApiException))
    cleanup_trials(jobs, {"KUBECONFIG": "/private/config", "AGENT_EVAL_K8S_NAMESPACE": "trials"})
    kube.new_client_from_config.assert_called_once_with("/private/config")
    client.V1Preconditions.assert_called_once_with(uid="owned-uid")
    assert core.delete_namespaced_pod.call_args.args[:2] == ("aeh-case-abc-env", "trials")
    core.list_namespaced_pod.assert_not_called()


def test_invalid_trial_ownership_stops_cleanup(tmp_path):
    path = trial(tmp_path)
    (path / "config.json").write_text('{"trial_name": "someone-else"}')
    with pytest.raises(CleanupError):
        cleanup_trials(tmp_path, {})
