"""Harbor task generation and Kubernetes execution adapter."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from abevalflow.execution.artifacts import (
    materialize_aeh_case_outputs,
)
from abevalflow.execution.base import BaseRunner, RunnerError
from abevalflow.execution.cleanup import cleanup_trials
from abevalflow.execution.cluster import trial_environment
from abevalflow.results.run import write_json


class EvaluationTimeout(TimeoutError):
    """The native AEH invocation or one of its trials exhausted timeout retries."""


def trial_timed_out(jobs: Path) -> bool:
    """Recognize native runtime timeout evidence, never a quality verdict or message substring."""
    timeout_types = {
        "AgentTimeoutError",
        "AgentSetupTimeoutError",
        "EnvironmentStartTimeoutError",
        "VerifierTimeoutError",
    }
    for path in jobs.glob("*/*/result.json"):
        try:
            info = json.loads(path.read_text()).get("exception_info") or {}
            if info.get("exception_type") in timeout_types:
                return True
        except (OSError, ValueError, TypeError, AttributeError):
            continue  # Existing evidence validation handles other runtime failures.
    return False


class HarborRunner(BaseRunner):
    """Harbor/Kubernetes execution backend.

    Invokes the complete upstream AEH distribution through its native commands.
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

        runtime = self.aeh_runtime
        tasks_dir = tasks_dir or output / "tasks"
        jobs_dir = jobs_dir or output / "jobs"
        tasks_prepared = bool(self.image)
        if tasks_prepared:
            self._prepare_enriched_tasks(config, Path(tasks_dir))
        cmd = [
            "-m",
            "agent_eval.harbor.run",
            "--config",
            str(config.resolve()),
            "--output",
            str(output.resolve()),
            "--tasks-dir",
            str(Path(tasks_dir).resolve()),
            "--jobs-dir",
            str(Path(jobs_dir).resolve()),
            "--env",
            self.env_type,
            "--model",
            self.model,
            "--n-concurrent",
            "1",
        ]
        if self.image:
            cmd.extend(["--image", self.image])
        if self.judge_model and not tasks_prepared:
            cmd.extend(["--judge-model", self.judge_model])
        code = self._run_with_timeout_retries(runtime, cmd, config, output, Path(jobs_dir))

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

        return code

    def _run_with_timeout_retries(self, runtime, cmd, config: Path, output: Path, jobs: Path) -> int:
        """Retry only the timed-out arm; scans, reviews, the other arm, and publication run once."""
        attempts = []
        history = output.parent / f"{output.name}-attempts.json"
        for attempt in range(1, self.timeout_retries + 2):
            timed_out = False
            try:
                code = runtime.run(cmd, environment=self.environment, timeout=self.timeout, cwd=config.parent.resolve())
                timed_out = trial_timed_out(jobs)
            except subprocess.TimeoutExpired:
                timed_out = True
                code = None
            except KeyboardInterrupt:
                cleanup_trials(jobs, self.environment)
                raise
            record = {
                "attempt": attempt,
                "timed_out": timed_out,
                "exit_code": code,
                "finished_at": datetime.now(UTC).isoformat(),
            }
            attempts.append(record)
            write_json(history, attempts)
            if not timed_out:
                if code not in (0, 1):
                    cleanup_trials(jobs, self.environment)
                return code
            # run_process has already stopped the entire local process group.
            # Await remote deletion before another attempt OR another skill may start.
            cleanup_trials(jobs, self.environment)
            record["cleanup_completed"] = True
            write_json(history, attempts)
            if attempt > self.timeout_retries:
                raise EvaluationTimeout(f"AEH timed out after {attempt} attempt(s)")
            archive = output.parent / f"{output.name}-timeouts" / f"attempt-{attempt}"
            archive.mkdir(parents=True, exist_ok=False)
            if output.exists():
                shutil.move(str(output), archive / "output")
            if jobs.exists():
                shutil.move(str(jobs), archive / "jobs")
            output.mkdir(parents=True, exist_ok=True)
            print(f"AEH timeout: retrying attempt {attempt + 1}/{self.timeout_retries + 1}")
        raise AssertionError("Unreachable timeout retry state")

    def _prepare_enriched_tasks(self, config: Path, tasks_dir: Path) -> None:
        """Generate upstream tasks and prepare their application-owned inputs."""

        from abevalflow.harbor_extensions.aeh_task_enrichment import enrich_harbor_tasks

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

        runtime = self.aeh_runtime
        command = [
            str(runtime.task_generator),
            "--config",
            str(config.resolve()),
            "--out",
            str(tasks_dir.resolve()),
            "--image",
            self.image,
        ]
        if self.judge_model:
            command.extend(["--judge-model", self.judge_model])
        if runtime.run(command, environment=self.environment, timeout=self.timeout, cwd=config.parent.resolve()) != 0:
            raise RunnerError("AEH task generation failed")
        n = enrich_harbor_tasks(
            tasks_dir,
            config_path=Path(config),
            skill_model=self.model,
            judge_model=self.judge_model,
            runtime_env=trial_environment(self.environment if self.environment is not None else os.environ),
        )
        print(f"Enriched {n} Harbor task package(s) (skills + annotations + models/reward)")
