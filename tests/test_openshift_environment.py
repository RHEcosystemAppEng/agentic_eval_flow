"""Unit tests for OpenShiftEnvironment Harbor custom env plugin.

Stubs ``agent_eval.harbor.kubernetes`` so tests run without AEH installed.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Stub AEH KubernetesEnvironment before importing the extension module.
_k8s_mod = types.ModuleType("agent_eval.harbor.kubernetes")


class _FakeKubernetesEnvironment:
    def _pod_manifest(self, image: str, env: dict) -> dict:
        return {
            "spec": {
                "containers": [{"name": "main", "volumeMounts": [], "env": []}],
                "volumes": [],
            }
        }

    async def start(self, force_build: bool) -> None:
        return None


_k8s_mod.KubernetesEnvironment = _FakeKubernetesEnvironment
sys.modules.setdefault("agent_eval", types.ModuleType("agent_eval"))
sys.modules.setdefault("agent_eval.harbor", types.ModuleType("agent_eval.harbor"))
sys.modules["agent_eval.harbor.kubernetes"] = _k8s_mod

from abevalflow.harbor_extensions import OPENSHIFT_ENVIRONMENT_IMPORT_PATH  # noqa: E402
from abevalflow.harbor_extensions.openshift_environment import (  # noqa: E402
    _HARBOR_PATHS,
    OpenShiftEnvironment,
)


class TestImportPathConstant:
    def test_constant_points_at_openshift_environment(self):
        assert OPENSHIFT_ENVIRONMENT_IMPORT_PATH.endswith(":OpenShiftEnvironment")
        assert "openshift_environment" in OPENSHIFT_ENVIRONMENT_IMPORT_PATH


class TestPodManifest:
    def test_injects_empty_dir_mounts(self):
        env = OpenShiftEnvironment.__new__(OpenShiftEnvironment)
        manifest = OpenShiftEnvironment._pod_manifest(env, "img:latest", {})

        mounts = {m["mountPath"] for m in manifest["spec"]["containers"][0]["volumeMounts"]}
        vols = {v["name"] for v in manifest["spec"]["volumes"]}
        assert "/workspace" in mounts
        assert "/tmp" in mounts
        assert "workspace" in vols
        assert "tmp" in vols
        assert vols == {"workspace", "tmp"} or {"workspace", "tmp"}.issubset(vols)


class TestStart:
    @pytest.mark.asyncio
    async def test_ensures_harbor_paths(self):
        env = OpenShiftEnvironment.__new__(OpenShiftEnvironment)
        env._checked_exec = AsyncMock()
        await OpenShiftEnvironment.start(env, force_build=False)

        env._checked_exec.assert_awaited_once()
        cmd = env._checked_exec.await_args.args[0]
        assert cmd.startswith("mkdir -p ")
        for path in _HARBOR_PATHS:
            assert path in cmd


class TestDownloadDir:
    @pytest.mark.asyncio
    async def test_empty_stdout_raises(self, tmp_path: Path):
        env = OpenShiftEnvironment.__new__(OpenShiftEnvironment)
        env.exec = AsyncMock(return_value=MagicMock(return_code=0, stdout="", stderr=""))
        with pytest.raises(RuntimeError, match="empty archive"):
            await OpenShiftEnvironment.download_dir(env, "/logs/verifier", tmp_path / "out")

    @pytest.mark.asyncio
    async def test_nonzero_rc_raises(self, tmp_path: Path):
        env = OpenShiftEnvironment.__new__(OpenShiftEnvironment)
        env.exec = AsyncMock(return_value=MagicMock(return_code=1, stdout="", stderr="boom"))
        with pytest.raises(RuntimeError, match="rc=1"):
            await OpenShiftEnvironment.download_dir(env, "/logs/verifier", tmp_path / "out")


def test_preflight_accepts_harbor_026_kwargs_and_delegates_pinned_aeh(monkeypatch):
    called = []
    monkeypatch.setattr(
        _FakeKubernetesEnvironment, "preflight", classmethod(lambda cls: called.append(cls)), raising=False
    )
    OpenShiftEnvironment.preflight(kwargs={"mcp_run_uid": "run-id"})
    assert called == [OpenShiftEnvironment]


def test_managed_native_agent_home_is_writable_empty_dir():
    env = OpenShiftEnvironment.__new__(OpenShiftEnvironment)
    env._mcp_run_uid = "uid"
    env._mcp_run_name = "run"
    manifest = OpenShiftEnvironment._pod_manifest(env, "image", {})
    container = manifest["spec"]["containers"][0]
    assert {"name": "installed-agent", "mountPath": "/installed-agent"} in container["volumeMounts"]
    assert {"name": "installed-agent", "emptyDir": {}} in manifest["spec"]["volumes"]
    assert manifest["spec"]["automountServiceAccountToken"] is False


def test_generic_native_agent_home_mount_unchanged():
    env = OpenShiftEnvironment.__new__(OpenShiftEnvironment)
    manifest = OpenShiftEnvironment._pod_manifest(env, "image", {})
    assert not any(v["name"] == "installed-agent" for v in manifest["spec"]["volumes"])
