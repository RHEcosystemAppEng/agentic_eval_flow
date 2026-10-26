"""Harbor task generation and Kubernetes execution adapter."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from abevalflow.execution.artifacts import (
    materialize_aeh_case_outputs,
)
from abevalflow.execution.base import BaseRunner, RunnerError
from abevalflow.harbor_extensions import OPENSHIFT_ENVIRONMENT_IMPORT_PATH


class HarborRunner(BaseRunner):
    """Harbor/Kubernetes execution backend.

    Uses the packaged upstream adapter for containerized execution.
    """

    name = "harbor"

    def _execute(
        self,
        config: Path,
        output: Path,
        run_id: str | None = None,
        tasks_dir: Path | None = None,
        jobs_dir: Path | None = None,
        **opts: Any,
    ) -> int:
        """Execute via harbor.run."""

        if not self.model:
            raise RunnerError("Harbor runner requires --model. Pass --model explicitly via CLI or pipeline parameter.")

        patched_config = config
        use_patched_config = False
        if self.env_type == "kubernetes":
            patched_config = self._patch_eval_config_for_openshift(config, tasks_dir)
            use_patched_config = True

        tasks_already_enriched = tasks_dir is not None and self.image
        if tasks_already_enriched:
            self._prepare_enriched_tasks(patched_config, Path(tasks_dir))

        cmd = [
            sys.executable,
            "-m",
            "abevalflow.execution.upstream",
            "--config",
            str(patched_config),
            "--output",
            str(output),
        ]

        if use_patched_config:
            cmd.extend(["--environment-import-path", OPENSHIFT_ENVIRONMENT_IMPORT_PATH])
        else:
            cmd.extend(["--env", self.env_type])

        if self.model:
            cmd.extend(["--model", self.model])

        if self.judge_model and not tasks_already_enriched:
            cmd.extend(["--judge-model", self.judge_model])
        if self.image:
            cmd.extend(["--image", self.image])
        if tasks_dir:
            cmd.extend(["--tasks-dir", str(tasks_dir)])

            if not tasks_already_enriched and (self.judge_model or self.model or self.image):
                cmd.append("--regenerate")
        if jobs_dir:
            cmd.extend(["--jobs-dir", str(jobs_dir)])

        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, env=self.environment, timeout=self.timeout, stdout=sys.stderr)

        try:
            materialize_aeh_case_outputs(
                Path(config),
                Path(output),
                Path(jobs_dir) if jobs_dir else None,
            )
        except Exception as exc:
            print(f"WARN: failed to materialize AEH case outputs: {exc}")

        try:
            from abevalflow.harbor_extensions.aeh_paths import rewrite_harbor_job_dir

            rewritten = rewrite_harbor_job_dir(Path(output) / "run_result.json")
            if rewritten:
                print(f"Rewrote harbor_job_dir -> {rewritten}")
        except Exception as exc:
            print(f"WARN: failed to rewrite harbor_job_dir: {exc}")

        return result.returncode

    def _prepare_enriched_tasks(self, config: Path, tasks_dir: Path) -> None:
        """Generate Harbor tasks then apply Agentic Eval Flow skill/annotation fixes."""

        from agent_eval.config import EvalConfig
        from agent_eval.harbor import tasks as tasks_mod

        from abevalflow.harbor_extensions.aeh_task_enrichment import (
            enrich_harbor_tasks,
        )

        if tasks_dir.exists():
            for child in tasks_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        else:
            tasks_dir.mkdir(parents=True, exist_ok=True)

        try:
            from abevalflow.execution.mcp import swap_mock_mcp_servers

            swapped = swap_mock_mcp_servers(config.parent)
            if swapped:
                print(f"Swapped mock MCP server entries: {swapped}")
        except Exception as exc:
            raise RunnerError("Could not prepare required MCP fixtures") from exc

        from abevalflow.execution.upstream import configure_task_resources

        configure_task_resources()
        eval_config = EvalConfig.from_yaml(config)
        print(f"Generating Harbor tasks into {tasks_dir}")
        tasks_mod.generate_tasks(
            eval_config,
            Path(config),
            tasks_dir,
            self.image,
            judge_model=self.judge_model,
        )
        n = enrich_harbor_tasks(
            tasks_dir,
            config_path=Path(config),
            skill_model=self.model,
            judge_model=self.judge_model,
        )
        print(f"Enriched {n} Harbor task package(s) (skills + annotations + models/reward)")

    def _patch_eval_config_for_openshift(self, config: Path, tasks_dir: Path | None) -> Path:
        """Patch eval.yaml for OpenShift emptyDir support.

        Sets environment.type to a valid enum value (``kubernetes``). The custom
        OpenShiftEnvironment class is applied via ``--environment-import-path``.

        Args:
            config: Original eval.yaml path
            tasks_dir: Tasks directory (not used - kept for backward compatibility)

        Returns:
            Path to patched eval.yaml
        """

        with open(config) as f:
            eval_config = yaml.safe_load(f)

        if "environment" not in eval_config:
            eval_config["environment"] = {}

        eval_config["environment"]["type"] = "kubernetes"

        config_dir = config.parent.resolve()
        runner_cfg = eval_config.get("runner", {})
        if isinstance(runner_cfg.get("plugin_dirs"), list):
            runner_cfg["plugin_dirs"] = [
                str((config_dir / p).resolve()) if not Path(p).is_absolute() else p for p in runner_cfg["plugin_dirs"]
            ]

        patched_config = config.parent / f"{config.stem}-openshift.yaml"
        with open(patched_config, "w") as f:
            yaml.dump(eval_config, f)

        print(f"Patched eval config for OpenShift: {patched_config}")
        return patched_config
