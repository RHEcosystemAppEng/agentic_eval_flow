#!/usr/bin/env python3
"""Unified AEH runner - abstracts execution backend (harbor, vanilla, future).

This dispatcher provides a consistent interface for running AEH evaluations
regardless of the underlying execution mode. The pairwise comparison logic
is shared across all runners since score.py is execution-mode agnostic.

Usage:
    # Single run
    python run_aeh.py single --runner harbor --config eval.yaml --output /path/to/output

    # Pairwise run
    python run_aeh.py pairwise --runner harbor \
        --control-config eval-control.yaml \
        --treatment-config eval-treatment.yaml \
        --output /path/to/output

Environment variables (for kubernetes/harbor mode):
    AGENT_EVAL_K8S_CREDENTIALS_SECRET: Secret name for LLM credentials
    AGENT_EVAL_RUNS_DIR: Base directory for run outputs
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import yaml

from abevalflow.harbor_extensions import OPENSHIFT_ENVIRONMENT_IMPORT_PATH


class RunnerError(Exception):
    """Raised when a runner encounters an error."""


def eval_dir_name(config_path: Path, fallback: str | None = None) -> str:
    """Directory name under AGENT_EVAL_RUNS_DIR, matching EvalConfig.eval_name().

    Order: ``execution.skill`` / top-level ``skill``, then sanitized ``name``,
    then ``fallback`` (pipeline submission-name) or the config filename stem.

    Prompt-mode evals (no skill) such as openclaw-forge use ``name:
    forge-eval-rubrics``. The Tekton wrapper must use this leaf, not the
    submission directory name.
    """
    fallback_name = fallback or config_path.stem
    try:
        raw = yaml.safe_load(config_path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return fallback_name
    if not isinstance(raw, dict):
        return fallback_name

    exec_block = raw.get("execution") if isinstance(raw.get("execution"), dict) else {}
    skill = str(exec_block.get("skill") or "").strip() or str(raw.get("skill") or "").strip()
    if skill:
        return skill

    name = str(raw.get("name") or "").strip()
    if name and name not in {config_path.stem, "eval"}:
        sanitized = name.lower().replace(" ", "-")
        sanitized = "".join(c for c in sanitized if c.isalnum() or c in "._-")
        if sanitized:
            return sanitized

    return fallback_name


def resolve_aeh_run_dir(
    reports_root: Path,
    run_id: str,
    *,
    eval_name: str,
    submission_name: str | None = None,
) -> Path | None:
    """Find ``reports/<leaf>/<run_id>`` that contains summary.yaml or run_result.json.

    Looks at eval_name, then submission_name, then any leaf under reports_root.
    """
    leaves: list[str] = []
    for leaf in (eval_name, submission_name):
        if leaf and leaf not in leaves:
            leaves.append(leaf)
    candidates = [reports_root / leaf / run_id for leaf in leaves]
    if reports_root.is_dir():
        for child in sorted(reports_root.iterdir()):
            if child.is_dir():
                candidates.append(child / run_id)

    seen: set[Path] = set()
    for cand in candidates:
        try:
            key = cand.resolve()
        except OSError:
            key = cand
        if key in seen:
            continue
        seen.add(key)
        if (cand / "summary.yaml").is_file() or (cand / "run_result.json").is_file():
            return cand
    return None


def _sync_output_dir(requested: Path, actual: Path) -> None:
    """Copy harness results into ``--output`` when eval_name() used a different leaf."""
    if not actual.is_dir():
        return
    if not ((actual / "summary.yaml").is_file() or (actual / "run_result.json").is_file()):
        return
    try:
        if requested.resolve() == actual.resolve():
            return
    except OSError:
        pass
    if (requested / "summary.yaml").is_file() or (requested / "run_result.json").is_file():
        return
    print(f"WARN: OpenShell wrote {actual}; copying into --output {requested}")
    requested.mkdir(parents=True, exist_ok=True)
    shutil.copytree(actual, requested, dirs_exist_ok=True)


def _output_paths_from_config(config: Path) -> list[str]:
    """Read outputs[].path from eval.yaml (default: ['output'])."""
    try:
        raw = yaml.safe_load(config.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return ["output"]
    paths: list[str] = []
    for out in raw.get("outputs") or []:
        if isinstance(out, dict) and out.get("path"):
            paths.append(str(out["path"]).strip().strip("/"))
        elif isinstance(out, str) and out.strip():
            paths.append(out.strip().strip("/"))
    return paths or ["output"]


def _case_id_from_trial_dir(trial_dir: Path) -> str:
    """Harbor trial dirs are often case-001__AbCdEf; score.py wants case-001."""
    name = trial_dir.name
    return name.split("__", 1)[0] if "__" in name else name


def _iter_harbor_trial_dirs(jobs_dir: Path | None) -> list[Path]:
    """Return trial directories from the newest Harbor job under jobs_dir."""
    if jobs_dir is None or not jobs_dir.is_dir():
        return []
    job_dirs = sorted(
        (d for d in jobs_dir.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
    )
    if not job_dirs:
        return []
    job = job_dirs[-1]
    trials: list[Path] = []
    for child in job.iterdir():
        if not child.is_dir():
            continue
        if (child / "verifier").is_dir() or (child / "result.json").is_file():
            trials.append(child)
    return trials


def materialize_aeh_case_outputs(
    config: Path,
    output_dir: Path,
    jobs_dir: Path | None = None,
) -> int:
    """Copy Harbor verifier/<outputs.path> into output_dir/cases/<case_id>/<path>.

    AEH score.py pairwise expects ``cases/<case_id>/<outputs.path>/…`` under the
    run directory. Upstream Harbor only mirrors ``verifier/artifacts``, while
    generated test.sh writes ``verifier/<outputs.path>``.

    Returns:
        Number of distinct case directories that received at least one copy.
    """
    output_paths = _output_paths_from_config(config)
    materialized: set[str] = set()

    for trial in _iter_harbor_trial_dirs(jobs_dir):
        case_id = _case_id_from_trial_dir(trial)
        copied_any = False
        for out_path in output_paths:
            src = trial / "verifier" / out_path
            if not src.is_dir():
                # Fall back to verifier/artifacts when present
                art = trial / "verifier" / "artifacts"
                if art.is_dir() and any(art.iterdir()):
                    src = art
                else:
                    continue
            dst = output_dir / "cases" / case_id / out_path
            if dst.exists():
                shutil.rmtree(dst)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, dst)
            copied_any = True
        if copied_any:
            materialized.add(case_id)

    n = len(materialized)
    if n:
        print(f"Materialized case outputs for {n} case(s) under {output_dir / 'cases'}")
    else:
        print(
            f"WARN: no verifier/{'/'.join(output_paths)} (or artifacts) found under "
            f"jobs_dir={jobs_dir}; score.py pairwise will fail without cases/"
        )
    return n


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
    ):
        self.model = model
        self.judge_model = judge_model
        self.image = image
        self.env_type = env_type

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
        # Read skill name from treatment config for consistent directory naming
        skill_name = self._read_skill_name(treatment_config)

        control_run_id = f"control-{run_id}"
        treatment_run_id = f"treatment-{run_id}"

        control_dir = output_base / skill_name / control_run_id
        treatment_dir = output_base / skill_name / treatment_run_id

        # Create separate tasks/jobs dirs for control and treatment to avoid conflicts
        # Harbor requires these dirs and they must be distinct
        base_tmp = tasks_dir.parent if tasks_dir else output_base.parent / "_eval_tmp"
        control_tasks_dir = base_tmp / f"aeh-control-tasks-{run_id}"
        control_jobs_dir = base_tmp / f"aeh-control-jobs-{run_id}"
        treatment_tasks_dir = base_tmp / f"aeh-treatment-tasks-{run_id}"
        treatment_jobs_dir = base_tmp / f"aeh-treatment-jobs-{run_id}"

        for d in [control_tasks_dir, control_jobs_dir, treatment_tasks_dir, treatment_jobs_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # 1. Execute control (mode-specific)
        print(f"=== Running control: {control_config} -> {control_dir}")
        control_exit = self._execute(
            control_config,
            control_dir,
            run_id=control_run_id,
            tasks_dir=control_tasks_dir,
            jobs_dir=control_jobs_dir,
            **opts,
        )

        # Fail-fast if control didn't produce summary.yaml
        if not (control_dir / "summary.yaml").exists():
            raise RunnerError(f"Control run failed - no summary.yaml in {control_dir}")

        # 2. Execute treatment (mode-specific)
        print(f"=== Running treatment: {treatment_config} -> {treatment_dir}")
        treatment_exit = self._execute(
            treatment_config,
            treatment_dir,
            run_id=treatment_run_id,
            tasks_dir=treatment_tasks_dir,
            jobs_dir=treatment_jobs_dir,
            **opts,
        )

        # Fail-fast if treatment didn't produce summary.yaml
        if not (treatment_dir / "summary.yaml").exists():
            raise RunnerError(f"Treatment run failed - no summary.yaml in {treatment_dir}")

        # 3. Pairwise comparison (SHARED - same for all runners)
        # Set AGENT_EVAL_RUNS_DIR so score.py can find the run directories
        # score.py resolves: $AGENT_EVAL_RUNS_DIR/<skill>/<run-id>
        print("=== Running pairwise comparison")
        pairwise_exit = self._run_pairwise_comparison(
            control_run_id=control_run_id,
            treatment_run_id=treatment_run_id,
            treatment_config=treatment_config,
            output_base=output_base,
            score_py_path=score_py_path,
            judge=judge,
        )

        # Harbor writes report.html before pairwise; regenerate so HTML includes
        # the pairwise: section merged into treatment summary.yaml.
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
        if score_py_path is None:
            # Try common locations
            candidates = [
                Path("/opt/agent-eval-harness/skills/eval-run/scripts/score.py"),
                Path("skills/eval-run/scripts/score.py"),
                Path(os.environ.get("AEH_SCORE_PY", "")),
            ]
            for candidate in candidates:
                if candidate.exists():
                    score_py_path = candidate
                    break
            else:
                raise RunnerError("score.py not found. Set AEH_SCORE_PY or provide --score-py-path")

        cmd = [
            sys.executable,
            str(score_py_path),
            "pairwise",
            "--run-id",
            treatment_run_id,
            "--baseline",
            control_run_id,
            "--config",
            str(treatment_config),
        ]

        if judge:
            cmd.extend(["--judge", judge])

        # Set AGENT_EVAL_RUNS_DIR so score.py can resolve run directories
        # score.py looks for: $AGENT_EVAL_RUNS_DIR/<skill>/<run-id>
        env = os.environ.copy()
        env["AGENT_EVAL_RUNS_DIR"] = str(output_base)

        print(f"Running: {' '.join(cmd)}")
        print(f"  AGENT_EVAL_RUNS_DIR={output_base}")
        result = subprocess.run(cmd, env=env)
        return result.returncode

    def _find_aeh_script(self, name: str, score_py_path: Path | None = None) -> Path | None:
        """Locate an AEH eval-run script (score.py / report.py) beside score.py."""
        candidates: list[Path] = []
        if score_py_path is not None:
            candidates.append(score_py_path.parent / name)
        candidates.extend(
            [
                Path(f"/opt/agent-eval-harness/skills/eval-run/scripts/{name}"),
                Path(f"skills/eval-run/scripts/{name}"),
            ]
        )
        env_score = os.environ.get("AEH_SCORE_PY", "")
        if env_score:
            candidates.append(Path(env_score).parent / name)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

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
            sys.executable,
            str(report_py),
            "--run-id",
            treatment_run_id,
            "--baseline",
            control_run_id,
            "--config",
            str(treatment_config),
        ]
        env = os.environ.copy()
        env["AGENT_EVAL_RUNS_DIR"] = str(output_base)

        print("=== Regenerating treatment report.html with pairwise baseline")
        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, env=env)
        if result.returncode != 0:
            print(f"WARNING: report.py exited {result.returncode} — treatment report.html may lack pairwise section")

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


class HarborRunner(BaseRunner):
    """Harbor/Kubernetes execution backend.

    Uses python -m agent_eval.harbor.run for containerized execution.
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
        # Harbor requires --model; fail fast with clear message
        if not self.model:
            raise RunnerError("Harbor runner requires --model. Pass --model explicitly via CLI or pipeline parameter.")

        # For kubernetes environment, patch eval.yaml to use OpenShiftEnvironment
        # This adds emptyDir mounts for /workspace and /tmp required by OpenShift
        patched_config = config
        use_patched_config = False
        if self.env_type == "kubernetes":
            patched_config = self._patch_eval_config_for_openshift(config, tasks_dir)
            use_patched_config = True

        # Pre-generate + enrich task packages so Harbor skips bare upstream
        # generation (missing skills/ + annotations wiring in AEH v1.0.3).
        if tasks_dir is not None and self.image:
            self._prepare_enriched_tasks(patched_config, Path(tasks_dir))

        cmd = [
            sys.executable,
            "-m",
            "agent_eval.harbor.run",
            "--config",
            str(patched_config),
            "--output",
            str(output),
        ]

        # For patched config (kubernetes mode), pass --environment-import-path explicitly
        # to override agent_eval.harbor.run's default environment mapping
        if use_patched_config:
            cmd.extend(["--environment-import-path", OPENSHIFT_ENVIRONMENT_IMPORT_PATH])
        else:
            # For other modes, use --env to select environment
            cmd.extend(["--env", self.env_type])

        if self.model:
            cmd.extend(["--model", self.model])
        if self.judge_model:
            cmd.extend(["--judge-model", self.judge_model])
        if self.image:
            cmd.extend(["--image", self.image])
        if tasks_dir:
            cmd.extend(["--tasks-dir", str(tasks_dir)])
        if jobs_dir:
            cmd.extend(["--jobs-dir", str(jobs_dir)])

        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd)

        # Bridge Harbor verifier outputs into the cases/ layout score.py expects.
        # Use the original config for outputs[].path (patched config is equivalent).
        try:
            materialize_aeh_case_outputs(
                Path(config),
                Path(output),
                Path(jobs_dir) if jobs_dir else None,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort bridge; don't hide harbor rc
            print(f"WARN: failed to materialize AEH case outputs: {exc}")

        # AEH MLflow logger rejects absolute harbor_job_dir; store workspace-relative.
        try:
            from abevalflow.harbor_extensions.aeh_paths import rewrite_harbor_job_dir

            rewritten = rewrite_harbor_job_dir(Path(output) / "run_result.json")
            if rewritten:
                print(f"Rewrote harbor_job_dir -> {rewritten}")
        except Exception as exc:  # noqa: BLE001 — best-effort; don't hide harbor rc
            print(f"WARN: failed to rewrite harbor_job_dir: {exc}")

        return result.returncode

    def _prepare_enriched_tasks(self, config: Path, tasks_dir: Path) -> None:
        """Generate Harbor tasks then apply Agentic Eval Flow skill/annotation fixes."""
        import shutil

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
        import yaml

        # Load original config
        with open(config) as f:
            eval_config = yaml.safe_load(f)

        # Keep a valid enum type in YAML; the custom emptyDir environment is
        # selected via --environment-import-path (Harbor clears type when set).
        if "environment" not in eval_config:
            eval_config["environment"] = {}

        eval_config["environment"]["type"] = "kubernetes"

        # Write patched config to same directory as original to preserve relative paths
        # This is critical - if we write to a different directory, relative paths in
        # the config (like cases/, skills/, etc.) will break
        patched_config = config.parent / f"{config.stem}-openshift.yaml"
        with open(patched_config, "w") as f:
            yaml.dump(eval_config, f)

        print(f"Patched eval config for OpenShift: {patched_config}")
        return patched_config


class VanillaRunner(BaseRunner):
    """Vanilla (direct/local) execution backend.

    NOT YET IMPLEMENTED. Agent-eval-harness does not currently have a
    standalone CLI for local execution. The eval-run skill is designed
    to be driven by an LLM agent, not invoked directly.

    This runner exists as a placeholder for future local execution support.
    Use 'harbor' runner with --env podman for local containerized execution.
    """

    name = "vanilla"

    def _execute(
        self,
        config: Path,
        output: Path,
        run_id: str | None = None,
        **opts: Any,
    ) -> int:
        """Execute via vanilla mode - NOT YET IMPLEMENTED."""
        raise RunnerError(
            "Vanilla runner is not yet implemented. "
            "Agent-eval-harness does not have a standalone local CLI. "
            "Use 'harbor' runner instead:\n"
            "  - For Kubernetes: --runner harbor --env-type kubernetes\n"
            "  - For local containers: --runner harbor --env-type podman\n"
            "  - For OpenShell/OpenClaw: --runner openshell\n"
            "\n"
            "See: https://github.com/agent-eval-harness for AEH documentation."
        )


class OpenShellRunner(BaseRunner):
    """OpenShell execution backend (cluster OpenShell gateway + inner OpenClaw sandbox).

    Invokes ``python -m agent_eval.openshell.run``. Does not patch eval.yaml
    for OpenShift emptyDir and does not call harbor.run.

    OpenShell writes ``$AGENT_EVAL_RUNS_DIR/<eval-name>/<run-id>/`` where
    eval-name is EvalConfig.eval_name() (skill, else ``name``). The pipeline
    passes ``--output`` as that run directory (``reports/<eval-name>/<run-id>``),
    so AGENT_EVAL_RUNS_DIR is set to the grandparent (``reports/``). If
    ``--output``'s leaf does not match eval_name(), results are copied into
    ``--output`` after the run so callers still find summary.yaml there.
    """

    name = "openshell"

    def _config_with_judge_model(self, config: Path) -> Path:
        """Write a sibling eval.yaml with models.judge overridden for LiteLLM.

        Harbor applies ``--judge-model`` by rewriting the bundled eval.yaml.
        OpenShell scores in-process via score.py, which prefers models.judge
        over EVAL_JUDGE_MODEL, so the override must land in the config file.
        Dataset paths stay relative to the original parent directory.
        """
        if not self.judge_model:
            return config
        try:
            raw = yaml.safe_load(config.read_text()) or {}
        except (OSError, yaml.YAMLError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        models = raw.get("models")
        if not isinstance(models, dict):
            models = {}
            raw["models"] = models
        models["judge"] = self.judge_model
        patched = config.parent / f".eval.judge-override.{os.getpid()}.yaml"
        patched.write_text(yaml.safe_dump(raw, sort_keys=False))
        print(f"OpenShell judge override: models.judge={self.judge_model} ({patched})")
        return patched

    def _execute(
        self,
        config: Path,
        output: Path,
        run_id: str | None = None,
        **opts: Any,
    ) -> int:
        if not self.model:
            raise RunnerError(
                "OpenShell runner requires --model. Pass --model explicitly via CLI or pipeline parameter."
            )
        if not os.environ.get("AGENT_EVAL_OPENSHELL_IMAGE"):
            raise RunnerError(
                "OpenShell runner requires AGENT_EVAL_OPENSHELL_IMAGE (sandbox image the forge-saw gateway can pull)."
            )
        if not os.environ.get("OPENSHELL_GATEWAY_ENDPOINT"):
            raise RunnerError(
                "OpenShell runner requires OPENSHELL_GATEWAY_ENDPOINT (in-cluster forge-saw gateway URL)."
            )

        rid = run_id or output.name
        # Prefer an existing AGENT_EVAL_RUNS_DIR (Tekton sets reports/).
        # Otherwise --output is reports/<eval-name>/<run-id> → grandparent.
        env_runs = os.environ.get("AGENT_EVAL_RUNS_DIR")
        runs_dir = Path(env_runs) if env_runs else output.parent.parent
        env = os.environ.copy()
        env["AGENT_EVAL_RUNS_DIR"] = str(runs_dir)
        if self.judge_model:
            env["EVAL_JUDGE_MODEL"] = self.judge_model
        actual = runs_dir / eval_dir_name(config, fallback=output.parent.name) / rid
        patched = self._config_with_judge_model(config)

        cmd = [
            sys.executable,
            "-m",
            "agent_eval.openshell.run",
            "--config",
            str(patched),
            "--model",
            self.model,
            "--run-id",
            rid,
        ]

        print(f"Running: {' '.join(cmd)}")
        print(f"  AGENT_EVAL_RUNS_DIR={runs_dir}")
        print(f"  --output={output}")
        print(f"  actual run dir={actual}")
        print(f"  OPENSHELL_GATEWAY_ENDPOINT={env.get('OPENSHELL_GATEWAY_ENDPOINT')}")
        print(f"  AGENT_EVAL_OPENSHELL_IMAGE={env.get('AGENT_EVAL_OPENSHELL_IMAGE')}")
        try:
            result = subprocess.run(cmd, env=env)
        finally:
            if patched != config:
                patched.unlink(missing_ok=True)
        _sync_output_dir(output, actual)
        return result.returncode


# Registry of available runners
RUNNERS: dict[str, type[BaseRunner]] = {
    "harbor": HarborRunner,
    "vanilla": VanillaRunner,
    "openshell": OpenShellRunner,
}


def get_runner(name: str, **kwargs: Any) -> BaseRunner:
    """Get a runner instance by name.

    Args:
        name: Runner name (harbor, vanilla, etc.)
        **kwargs: Arguments passed to runner constructor

    Returns:
        Runner instance

    Raises:
        ValueError: If runner name is not recognized
    """
    if name not in RUNNERS:
        available = ", ".join(RUNNERS.keys())
        raise ValueError(f"Unknown runner '{name}'. Available: {available}")
    return RUNNERS[name](**kwargs)


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Unified AEH runner - abstracts execution backend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Common arguments
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--runner",
        choices=list(RUNNERS.keys()),
        default="harbor",
        help=(
            "Execution backend (default: harbor). "
            "'openshell' talks to a forge-saw gateway. "
            "'vanilla' raises RunnerError."
        ),
    )
    common.add_argument("--model", help="Model for skill execution")
    common.add_argument("--judge-model", help="Model for LLM judges")
    common.add_argument("--image", help="Container image for trial pods (harbor mode)")
    common.add_argument(
        "--env-type",
        default="kubernetes",
        help="Environment type for harbor mode (default: kubernetes)",
    )

    # Single run command
    single = subparsers.add_parser("single", parents=[common], help="Run single evaluation")
    single.add_argument("--config", required=True, help="Path to eval.yaml")
    single.add_argument("--output", required=True, help="Output directory")
    single.add_argument("--run-id", help="Run identifier")
    single.add_argument("--tasks-dir", help="Tasks directory (harbor mode)")
    single.add_argument("--jobs-dir", help="Jobs directory (harbor mode)")

    # Pairwise run command
    pairwise = subparsers.add_parser("pairwise", parents=[common], help="Run pairwise A/B comparison")
    pairwise.add_argument("--control-config", required=True, help="Path to control eval.yaml")
    pairwise.add_argument("--treatment-config", required=True, help="Path to treatment eval.yaml")
    pairwise.add_argument("--output", required=True, help="Base output directory")
    pairwise.add_argument("--run-id", required=True, help="Run identifier")
    pairwise.add_argument("--score-py-path", help="Path to score.py")
    pairwise.add_argument("--judge", default="pairwise", help="Judge name for comparison")
    pairwise.add_argument("--tasks-dir", help="Tasks directory (harbor mode)")
    pairwise.add_argument("--jobs-dir", help="Jobs directory (harbor mode)")

    args = parser.parse_args()

    try:
        runner = get_runner(
            args.runner,
            model=args.model,
            judge_model=args.judge_model,
            image=getattr(args, "image", None),
            env_type=getattr(args, "env_type", "kubernetes"),
        )

        if args.command == "single":
            exit_code = runner.run_single(
                config=Path(args.config),
                output=Path(args.output),
                run_id=args.run_id,
                tasks_dir=Path(args.tasks_dir) if args.tasks_dir else None,
                jobs_dir=Path(args.jobs_dir) if args.jobs_dir else None,
            )
            return exit_code

        elif args.command == "pairwise":
            result = runner.run_pairwise(
                control_config=Path(args.control_config),
                treatment_config=Path(args.treatment_config),
                output_base=Path(args.output),
                run_id=args.run_id,
                score_py_path=Path(args.score_py_path) if args.score_py_path else None,
                judge=args.judge,
                tasks_dir=Path(args.tasks_dir) if args.tasks_dir else None,
                jobs_dir=Path(args.jobs_dir) if args.jobs_dir else None,
            )

            # Print results summary
            print("\n=== Pairwise Results ===")
            print(f"Control: {result['control_dir']} (exit: {result['control_exit']})")
            print(f"Treatment: {result['treatment_dir']} (exit: {result['treatment_exit']})")
            print(f"Pairwise exit: {result['pairwise_exit']}")

            # Return pairwise exit code (0 = pass, 1 = regression)
            return result["pairwise_exit"]

    except RunnerError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
