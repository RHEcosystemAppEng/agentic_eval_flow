"""Single and pairwise execution contracts and comparison lifecycle."""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from abevalflow.execution.artifacts import eval_dir_name
from abevalflow.execution.runtime import resolve_runtime


class RunnerError(Exception):
    """Raised when a runner encounters an error."""


class BaseRunner(ABC):
    """Base class for AEH execution backends.

    All runners share the same pairwise comparison logic since score.py
    is execution-mode agnostic - it only cares about output directories.
    """

    name: str = "base"

    def __init__(
        self,
        model: str | None = None,
        judge_model: str | None = None,
        image: str | None = None,
        env_type: str = "kubernetes",
        environment: dict[str, str] | None = None,
        timeout: int = 7200,
        aeh_root: Path | None = None,
        aeh_python: Path | None = None,
        timeout_retries: int = 1,
        n_concurrent: int = 1,
    ):
        self.model = model
        self.judge_model = judge_model
        self.image = image
        self.env_type = env_type
        self.environment = environment
        self.timeout = timeout
        self.aeh_root = aeh_root
        self.aeh_python = aeh_python
        self.timeout_retries = timeout_retries
        self.n_concurrent = n_concurrent

    @property
    def aeh_runtime(self):
        return resolve_runtime(self.aeh_root, self.aeh_python)

    def run_single(
        self,
        config: Path,
        output: Path,
        run_id: str | None = None,
        **opts: Any,
    ) -> int:
        """Run a single evaluation.

        Args:
            config: Path to eval.yaml
            output: Output directory for results
            run_id: Optional run identifier
            **opts: Additional runner-specific options

        Returns:
            Exit code (0 = success, 1 = threshold warning, other = error)
        """
        output.mkdir(parents=True, exist_ok=True)
        return self._execute(config, output, run_id=run_id, **opts)

    def run_pairwise(
        self,
        control_config: Path,
        treatment_config: Path,
        output_base: Path,
        run_id: str,
        score_py_path: Path | None = None,
        judge: str | None = None,
        tasks_dir: Path | None = None,
        jobs_dir: Path | None = None,
        **opts: Any,
    ) -> dict[str, Any]:
        """Run pairwise A/B comparison.

        Executes control and treatment runs, then runs pairwise comparison.
        The pairwise step is SHARED across all runners.

        Args:
            control_config: Path to control variant eval.yaml
            treatment_config: Path to treatment variant eval.yaml
            output_base: Base output directory
            run_id: Run identifier (used for directory naming)
            score_py_path: Path to score.py (auto-detected if None)
            judge: Judge name to use for pairwise comparison
            tasks_dir: Base tasks directory (separate control/treatment subdirs created)
            jobs_dir: Base jobs directory (separate control/treatment subdirs created)
            **opts: Additional runner-specific options

        Returns:
            Dictionary with control_dir, treatment_dir, exit_codes, pairwise_exit
        """

        skill_name = self._read_skill_name(treatment_config)

        control_run_id = f"control-{run_id}"
        treatment_run_id = f"treatment-{run_id}"

        control_dir = output_base / skill_name / control_run_id
        treatment_dir = output_base / skill_name / treatment_run_id

        base_tmp = tasks_dir.parent if tasks_dir else output_base.parent / "_eval_tmp"
        control_tasks_dir = base_tmp / f"aeh-control-tasks-{run_id}"
        control_jobs_dir = base_tmp / f"aeh-control-jobs-{run_id}"
        treatment_tasks_dir = base_tmp / f"aeh-treatment-tasks-{run_id}"
        treatment_jobs_dir = base_tmp / f"aeh-treatment-jobs-{run_id}"

        for d in [control_tasks_dir, control_jobs_dir, treatment_tasks_dir, treatment_jobs_dir]:
            d.mkdir(parents=True, exist_ok=True)

        print(f"=== Running control: {control_config} -> {control_dir}")
        control_exit = self._execute(
            control_config,
            control_dir,
            run_id=control_run_id,
            tasks_dir=control_tasks_dir,
            jobs_dir=control_jobs_dir,
            **opts,
        )

        if not (control_dir / "summary.yaml").exists():
            raise RunnerError(f"Control run failed - no summary.yaml in {control_dir}")

        print(f"=== Running treatment: {treatment_config} -> {treatment_dir}")
        treatment_exit = self._execute(
            treatment_config,
            treatment_dir,
            run_id=treatment_run_id,
            tasks_dir=treatment_tasks_dir,
            jobs_dir=treatment_jobs_dir,
            **opts,
        )

        if not (treatment_dir / "summary.yaml").exists():
            raise RunnerError(f"Treatment run failed - no summary.yaml in {treatment_dir}")

        print("=== Running pairwise comparison")
        pairwise_exit = self._run_pairwise_comparison(
            control_run_id=control_run_id,
            treatment_run_id=treatment_run_id,
            treatment_config=treatment_config,
            output_base=output_base,
            score_py_path=score_py_path,
            judge=judge,
        )

        if pairwise_exit == 0:
            self._regenerate_report_with_baseline(
                treatment_run_id=treatment_run_id,
                control_run_id=control_run_id,
                treatment_config=treatment_config,
                output_base=output_base,
                score_py_path=score_py_path,
            )

        return {
            "control_dir": str(control_dir),
            "treatment_dir": str(treatment_dir),
            "control_exit": control_exit,
            "treatment_exit": treatment_exit,
            "pairwise_exit": pairwise_exit,
            "skill_name": skill_name,
            "control_run_id": control_run_id,
            "treatment_run_id": treatment_run_id,
        }

    def _run_pairwise_comparison(
        self,
        control_run_id: str,
        treatment_run_id: str,
        treatment_config: Path,
        output_base: Path,
        score_py_path: Path | None = None,
        judge: str | None = None,
    ) -> int:
        """Run score.py pairwise - shared across all runners.

        Args:
            control_run_id: Control run identifier
            treatment_run_id: Treatment run identifier
            treatment_config: Path to treatment config (contains judge definitions)
            output_base: Base output directory (set as AGENT_EVAL_RUNS_DIR)
            score_py_path: Path to score.py (auto-detected if None)
            judge: Judge name to use (default: "pairwise")

        Returns:
            Exit code from score.py pairwise
        """
        score_py_path = score_py_path or self.aeh_runtime.script("score.py")
        if score_py_path is None:
            raise RunnerError("score.py not found. Set AEH_SCORE_PY or provide --score-script")

        cmd = [
            str(score_py_path.resolve()),
            "pairwise",
            "--run-id",
            treatment_run_id,
            "--baseline",
            control_run_id,
            "--config",
            str(treatment_config.resolve()),
        ]

        if judge:
            cmd.extend(["--judge", judge])

        env = (self.environment or os.environ).copy()
        env["AGENT_EVAL_RUNS_DIR"] = str(output_base.resolve())

        print(f"Running: {' '.join(cmd)}")
        print(f"  AGENT_EVAL_RUNS_DIR={output_base}")
        return self.aeh_runtime.run(cmd, environment=env, timeout=self.timeout, cwd=treatment_config.parent.resolve())

    def _find_aeh_script(self, name: str, score_py_path: Path | None = None) -> Path | None:
        """Locate an AEH eval-run script (score.py / report.py) beside score.py."""
        path = score_py_path.parent / name if score_py_path else self.aeh_runtime.script(name)
        return path if path.is_file() else None

    def _regenerate_report_with_baseline(
        self,
        treatment_run_id: str,
        control_run_id: str,
        treatment_config: Path,
        output_base: Path,
        score_py_path: Path | None = None,
    ) -> None:
        """Rewrite treatment report.html including pairwise vs control baseline.

        Best-effort: log and continue if report.py is missing or fails.
        """
        report_py = self._find_aeh_script("report.py", score_py_path=score_py_path)
        if report_py is None:
            print("WARNING: report.py not found — skipping pairwise HTML regenerate")
            return

        cmd = [
            str(report_py.resolve()),
            "--run-id",
            treatment_run_id,
            "--baseline",
            control_run_id,
            "--config",
            str(treatment_config.resolve()),
        ]
        env = (self.environment or os.environ).copy()
        env["AGENT_EVAL_RUNS_DIR"] = str(output_base.resolve())

        print("=== Regenerating treatment report.html with pairwise baseline")
        print(f"Running: {' '.join(cmd)}")
        code = self.aeh_runtime.run(cmd, environment=env, timeout=self.timeout, cwd=treatment_config.parent.resolve())
        if code != 0:
            print(f"WARNING: report.py exited {code} — treatment report.html may lack pairwise section")

    def _read_skill_name(self, config_path: Path) -> str:
        """Read reports-leaf name from eval.yaml (skill, then name, then stem)."""
        return eval_dir_name(config_path, fallback=config_path.stem)

    @abstractmethod
    def _execute(
        self,
        config: Path,
        output: Path,
        run_id: str | None = None,
        **opts: Any,
    ) -> int:
        """Execute evaluation - mode-specific implementation.

        Args:
            config: Path to eval.yaml
            output: Output directory
            run_id: Optional run identifier
            **opts: Additional options

        Returns:
            Exit code
        """
        ...
