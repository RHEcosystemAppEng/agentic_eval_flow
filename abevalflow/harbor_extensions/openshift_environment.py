"""OpenShift-compatible KubernetesEnvironment for Harbor trials.

Extends agent-eval-harness KubernetesEnvironment with:

1. emptyDir mounts for /workspace and /tmp (RO-rootfs-friendly workdirs)
2. Ensuring Harbor EnvironmentPaths exist after pod start — required because
   Harbor shared-verifier mode redirects test stdout to
   ``/logs/verifier/test-stdout.txt`` *before* ``test.sh`` runs. If that
   directory is missing, the redirect fails, ``test.sh`` never executes, and
   ``download_dir`` returns empty stdout → ``DownloadVerifierDirError``.

Usage (classic Harbor A/B and AEH):

    environment:
      import_path: abevalflow.harbor_extensions.openshift_environment:OpenShiftEnvironment
      delete: true

Prebuilt trial images: set ``docker_image`` in the task's ``task.toml``
``[environment]`` section (not fork ``kwargs.image_ref``).
"""

from __future__ import annotations

import base64
import io
import logging
import shlex
import tarfile
from pathlib import Path

from agent_eval.harbor.kubernetes import KubernetesEnvironment

logger = logging.getLogger(__name__)

# Harbor EnvironmentPaths (+ AEH workdirs) that must exist before verifier runs.
_HARBOR_PATHS = (
    "/logs/agent",
    "/logs/verifier",
    "/logs/artifacts",
    "/tests",
    "/solution",
    "/workspace",
    "/tmp",
)


class OpenShiftEnvironment(KubernetesEnvironment):
    """KubernetesEnvironment with OpenShift trial-pod prep."""

    def _pod_manifest(self, image: str, env: dict) -> dict:
        """Build pod manifest with emptyDir volumes for writable workdirs."""
        # Skip SSL verification for corporate proxies with self-signed certs.
        # NODE_TLS_REJECT_UNAUTHORIZED covers Claude Code (Node.js); Python's
        # httpx/requests clients used by the LLM judge (agent_eval.harbor.reward)
        # honor neither this nor PYTHONHTTPSVERIFY, so AEH_INSECURE_SSL triggers
        # the sitecustomize.py monkeypatch baked into the AEH image instead.
        env.setdefault("NODE_TLS_REJECT_UNAUTHORIZED", "0")
        env.setdefault("PYTHONHTTPSVERIFY", "0")
        env.setdefault("AEH_INSECURE_SSL", "1")

        manifest = super()._pod_manifest(image, env)

        pod_spec = manifest["spec"]
        container = pod_spec["containers"][0]

        # The K8s credentials secret (AGENT_EVAL_K8S_CREDENTIALS_SECRET) is
        # mounted wholesale via envFrom, exposing only its literal key name
        # ("api-key" by convention here — see evaluate.yaml). Claude Code
        # never needs ANTHROPIC_API_KEY from pod env (its key is baked into
        # the task package's .claude/settings.json at generation time), but
        # agent_eval.harbor.reward's LLM judge runs *inside this pod* and
        # reads os.environ["ANTHROPIC_API_KEY"] directly — without this it
        # silently falls through to an unconfigured MLflow judge fallback.
        self._expose_llm_judge_api_key(container)

        container.setdefault("volumeMounts", []).extend(
            [
                {"name": "workspace", "mountPath": "/workspace"},
                {"name": "tmp", "mountPath": "/tmp"},
            ]
        )
        pod_spec.setdefault("volumes", []).extend(
            [
                {"name": "workspace", "emptyDir": {}},
                {"name": "tmp", "emptyDir": {}},
            ]
        )

        mount_paths = [m.get("mountPath") for m in container.get("volumeMounts", [])]
        logger.info("OpenShiftEnvironment injected mounts: %s", mount_paths)

        return manifest

    @staticmethod
    def _expose_llm_judge_api_key(container: dict) -> None:
        """Add ANTHROPIC_API_KEY sourced from the mounted credentials secret.

        Discovers the secret name from the container's own envFrom (set by
        the base KubernetesEnvironment from AGENT_EVAL_K8S_CREDENTIALS_SECRET)
        instead of hardcoding it, so this works regardless of which secret
        the pipeline configures. No-ops if ANTHROPIC_API_KEY is already set
        explicitly, or if no credentials secret is mounted.
        """
        env_list = container.setdefault("env", [])
        if any(e.get("name") == "ANTHROPIC_API_KEY" for e in env_list):
            return

        secret_name = next(
            (
                (ef.get("secretRef") or {}).get("name")
                for ef in container.get("envFrom", [])
                if (ef.get("secretRef") or {}).get("name")
            ),
            None,
        )
        if not secret_name:
            return

        env_list.append(
            {
                "name": "ANTHROPIC_API_KEY",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": secret_name,
                        "key": "api-key",
                        "optional": True,
                    }
                },
            }
        )

    async def start(self, force_build: bool) -> None:
        """Start the pod, then create Harbor paths needed for verifier redirect."""
        await super().start(force_build)
        dirs = " ".join(shlex.quote(p) for p in _HARBOR_PATHS)
        # mkdir only — image paths are already group-writable; chmod can fail
        # with EPERM under OpenShift SCC-assigned UIDs (including on /solution).
        await self._checked_exec(
            f"mkdir -p {dirs}",
            "ensure Harbor EnvironmentPaths before verifier redirect",
        )
        logger.info("Ensured Harbor paths exist: %s", ", ".join(_HARBOR_PATHS))

    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        """Download a remote directory using pipefail so missing dirs fail clearly.

        Parent implementation uses ``tar | base64`` without pipefail, so a missing
        source directory yields empty stdout with exit code 0 and then
        ``tarfile.ReadError: empty file`` wrapped as DownloadVerifierDirError.
        """
        res = await self.exec(f"set -o pipefail; tar cf - -C {shlex.quote(source_dir)} . | base64 -w0")
        if res.return_code != 0:
            raise RuntimeError(f"download_dir {source_dir}: rc={res.return_code} stderr={res.stderr}")
        if not res.stdout:
            raise RuntimeError(
                f"download_dir {source_dir}: empty archive (directory missing or tar produced no stdout)"
            )
        Path(target_dir).mkdir(parents=True, exist_ok=True)
        raw = base64.b64decode(res.stdout)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r") as tf:
            tf.extractall(target_dir, filter="data")
