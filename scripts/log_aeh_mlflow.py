"""Log AEH Harbor run results to MLflow (post-evaluate, non-blocking caller).

Requires an importable ``mlflow`` package in the current Python environment
(Tekton evaluate installs ``mlflow-skinny`` + ``pandas`` into ``/tmp`` when
missing). Prefers upstream AEH ``skills/eval-mlflow/scripts/log_results.py``
when present (under ``/opt/agent-eval-harness`` or ``AGENT_EVAL_HARNESS_ROOT``).
That is the **AEH** logger (metrics, artifacts, traces from stdout.log).
Falls back to a minimal **CI** metrics logger from ``run_result.json`` +
``summary.yaml`` when upstream is absent or no-ops.

Prompt-mode eval.yaml (``execution.prompt``) is patched via ``name`` /
``mlflow.experiment`` only — never a top-level ``skill:``, which AEH rejects.

Optional actions (same AEH skill tree):
  - ``push-feedback`` — attach judge feedback to traces (``attach_feedback.py``)
  - ``sync-dataset`` — sync cases to the MLflow dataset registry when a schema
    mapping is available or a simple ``input.yaml:prompt`` default applies

Experiment naming
-----------------
Pass ``--experiment <pipeline-run-id>`` so **one PipelineRun maps to one
MLflow experiment**. Control/treatment (or N attempts) are separate MLflow
*runs* under that experiment (``--run-id``), not separate experiments.

When ``--experiment`` is set we temporarily patch the eval config so AEH
scripts use that experiment name and the reports skill directory that
actually contains ``--run-id`` (pairwise control yaml skill names often
differ from the shared reports folder).

Usage::

    python scripts/log_aeh_mlflow.py \\
        --run-id <pipeline-run-id or treatment-<id>> \\
        --config /path/to/eval.yaml \\
        --runs-dir /workspace/source/reports \\
        --tracking-uri http://mlflow.example:5000 \\
        --experiment <pipeline-run-id> \\
        [--actions log-results,push-feedback,sync-dataset]

Skip (exit 0) when ``--enabled false`` or tracking URI is empty.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_AEH_MLFLOW_SCRIPTS = Path("/opt/agent-eval-harness/skills/eval-mlflow/scripts")
_DEFAULT_ACTIONS = ("log-results", "push-feedback", "sync-dataset")
# Upstream log_results.py wording varies; also catch ImportError-style lines.
_MISSING_MLFLOW_MARKERS = (
    "MLflow not installed",
    "mlflow is not installed",
    "No module named 'mlflow'",
    'No module named "mlflow"',
)


def _aeh_script(name: str) -> Path | None:
    root = os.environ.get("AGENT_EVAL_HARNESS_ROOT", "").strip()
    candidates: list[Path] = []
    if root:
        candidates.append(Path(root) / "skills/eval-mlflow/scripts" / name)
    candidates.append(_AEH_MLFLOW_SCRIPTS / name)
    for path in candidates:
        if path.is_file():
            return path
    return None


def _resolve_log_results_script() -> Path | None:
    return _aeh_script("log_results.py")


def _parse_actions(raw: str) -> list[str]:
    actions = [a.strip() for a in (raw or "").split(",") if a.strip()]
    return actions or list(_DEFAULT_ACTIONS)


def _variant_from_run_id(run_id: str) -> str | None:
    if run_id.startswith("control-"):
        return "control"
    if run_id.startswith("treatment-"):
        return "treatment"
    return None


def _discover_run_dir(runs_dir: Path, run_id: str) -> Path | None:
    """Find ``runs_dir/<skill>/<run_id>`` by scanning skill directories."""
    if not runs_dir.is_dir():
        return None
    exact_hits: list[Path] = []
    for skill_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        candidate = skill_dir / run_id
        if candidate.is_dir():
            exact_hits.append(candidate)
    if len(exact_hits) == 1:
        return exact_hits[0]
    if len(exact_hits) > 1:
        logger.warning(
            "Multiple report dirs for run_id=%r; using first: %s",
            run_id,
            exact_hits[0],
        )
        return exact_hits[0]
    return None


def _resolve_run_dir(runs_dir: Path, skill: str, run_id: str) -> Path:
    """Locate report dir for ``run_id``, tolerating skill / pairwise mismatches.

    Order:
    1. ``runs_dir/<skill>/<run_id>``
    2. Pairwise prefixes under ``skill`` when ``run_id`` is bare
    3. Scan all skill dirs under ``runs_dir`` for ``<run_id>``
    """
    candidates = [runs_dir / skill / run_id]
    if not run_id.startswith(("control-", "treatment-")):
        candidates.append(runs_dir / skill / f"treatment-{run_id}")
        candidates.append(runs_dir / skill / f"control-{run_id}")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate

    discovered = _discover_run_dir(runs_dir, run_id)
    if discovered is not None:
        return discovered

    tried = ", ".join(str(c) for c in candidates)
    raise FileNotFoundError(f"AEH run dir not found for skill={skill!r} run_id={run_id!r}; tried: {tried}")


def _reports_skill_name(runs_dir: Path, config_skill: str, run_id: str) -> str:
    """Skill folder name under reports/ that contains ``run_id``."""
    try:
        run_dir = _resolve_run_dir(runs_dir, config_skill, run_id)
    except FileNotFoundError:
        return config_skill
    return run_dir.parent.name


def _config_reports_leaf(raw: dict, fallback: str) -> str:
    """Match AEH EvalConfig.eval_name(): skill, else sanitized name, else fallback."""
    skill = str(raw.get("skill") or "").strip()
    if skill:
        return skill
    name = str(raw.get("name") or "").strip()
    if name:
        sanitized = name.lower().replace(" ", "-")
        sanitized = "".join(c for c in sanitized if c.isalnum() or c in "._-")
        if sanitized:
            return sanitized
    return fallback


def _is_prompt_mode(raw: dict) -> bool:
    execution = raw.get("execution") if isinstance(raw.get("execution"), dict) else {}
    return bool(str(execution.get("prompt") or "").strip())


def _apply_mlflow_eval_patch(
    raw: dict,
    *,
    experiment: str,
    reports_skill: str,
    tracking_uri: str = "",
) -> dict:
    """Stamp experiment/URI for AEH log_results.py without breaking prompt-mode evals.

    Top-level ``skill:`` plus ``execution.prompt`` is rejected by EvalConfig.
    Prompt-mode submissions (openclaw-forge) keep ``name`` as the reports leaf.
    """
    patched = dict(raw)
    if _is_prompt_mode(patched):
        patched.pop("skill", None)
        patched["name"] = reports_skill
    else:
        patched["skill"] = reports_skill
        if "name" in patched:
            patched["name"] = reports_skill
    mlflow_cfg = patched.get("mlflow") if isinstance(patched.get("mlflow"), dict) else {}
    mlflow_cfg = dict(mlflow_cfg)
    mlflow_cfg["experiment"] = experiment
    uri = (tracking_uri or "").strip()
    if uri:
        mlflow_cfg["tracking_uri"] = uri
    patched["mlflow"] = mlflow_cfg
    return patched


def _write_patched_config(
    config: Path,
    *,
    experiment: str,
    reports_skill: str,
    tracking_uri: str = "",
) -> Path:
    """Write a temp eval.yaml forcing AEH experiment + reports skill path."""
    raw = yaml.safe_load(config.read_text()) or {}
    if not isinstance(raw, dict):
        raw = {}
    raw = _apply_mlflow_eval_patch(
        raw,
        experiment=experiment,
        reports_skill=reports_skill,
        tracking_uri=tracking_uri,
    )

    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".yaml",
        delete=False,
        prefix="aeh-mlflow-config-",
    )
    yaml.safe_dump(raw, tmp, sort_keys=False, allow_unicode=True)
    tmp.close()
    path = Path(tmp.name)
    logger.info(
        "Patched AEH MLflow config: experiment=%s reports_leaf=%s prompt_mode=%s -> %s",
        experiment,
        reports_skill,
        _is_prompt_mode(raw),
        path,
    )
    return path


def _default_schema_mapping(config: Path) -> dict | None:
    """Build a minimal mapping when cases expose ``input.yaml`` with ``prompt``."""
    try:
        raw = yaml.safe_load(config.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return None
    dataset = raw.get("dataset") if isinstance(raw.get("dataset"), dict) else {}
    rel = str(dataset.get("path") or "cases")
    cases_dir = (config.parent / rel).resolve()
    if not cases_dir.is_dir():
        return None
    for case in sorted(p for p in cases_dir.iterdir() if p.is_dir()):
        input_yaml = case / "input.yaml"
        if not input_yaml.is_file():
            continue
        try:
            data = yaml.safe_load(input_yaml.read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue
        if isinstance(data, dict) and "prompt" in data:
            mapping: dict = {"inputs": {"prompt": "input.yaml:prompt"}, "expectations": {}}
            # Optional gold files commonly used by AEH samples
            for name in ("reference.md", "reference.yaml"):
                if (case / name).is_file():
                    mapping["expectations"]["reference"] = f"{name}:__file__"
                    break
            return mapping
    return None


def _run_aeh_script(script: Path, argv: list[str], *, runs_dir: Path) -> int:
    env = os.environ.copy()
    env["AGENT_EVAL_RUNS_DIR"] = str(runs_dir)
    cmd = [sys.executable, str(script), *argv]
    logger.info("Running AEH MLflow script: %s", " ".join(cmd))
    result = subprocess.run(cmd, env=env, check=False, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def _maybe_sync_dataset(*, config: Path, runs_dir: Path) -> None:
    script = _aeh_script("sync_dataset.py")
    if script is None:
        logger.info("sync_dataset.py not found; skipping sync-dataset")
        return

    mapping_path = config.parent / "schema_mapping.json"
    # Patched temp configs live outside the submission; map from original parent
    # is handled by caller passing the original config's mapping via sibling.
    tmp_path: Path | None = None
    if mapping_path.is_file():
        logger.info("Using schema mapping: %s", mapping_path)
    else:
        mapping = _default_schema_mapping(config)
        if mapping is None:
            # When config is a temp patch, try schema next to nothing — skip.
            logger.info("No schema_mapping.json and no default input.yaml:prompt mapping; skipping sync-dataset")
            return
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, prefix="aeh-schema-mapping-")
        json.dump(mapping, tmp)
        tmp.close()
        tmp_path = Path(tmp.name)
        mapping_path = tmp_path
        logger.info("Using auto-generated schema mapping for sync-dataset")

    try:
        rc = _run_aeh_script(
            script,
            ["--config", str(config), "--mapping", str(mapping_path)],
            runs_dir=runs_dir,
        )
        if rc != 0:
            logger.warning("sync-dataset exited %s (non-blocking)", rc)
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)


def _maybe_push_feedback(*, run_id: str, config: Path, runs_dir: Path) -> None:
    script = _aeh_script("attach_feedback.py")
    if script is None:
        logger.info("attach_feedback.py not found; skipping push-feedback")
        return
    rc = _run_aeh_script(
        script,
        [
            "--run-id",
            run_id,
            "--config",
            str(config),
            "--action",
            "push",
            "--source",
            "judge",
        ],
        runs_dir=runs_dir,
    )
    if rc != 0:
        logger.warning("push-feedback exited %s (non-blocking)", rc)


def _mlflow_importable() -> bool:
    try:
        import mlflow  # noqa: F401
    except ImportError:
        return False
    return True


def _upstream_noop_missing_mlflow(combined: str) -> bool:
    """True when upstream log_results output indicates mlflow was missing.

    Relies on known upstream / ImportError message markers only. A structural
    ``returncode == 0 and not importable`` guard is intentionally avoided: it
    false-positives in test envs (and any interpreter) where mlflow is absent
    but output is a real success string such as ``Logged N runs``.
    """
    return any(marker in combined for marker in _MISSING_MLFLOW_MARKERS)


def _run_aeh_log_results(*, script: Path, run_id: str, config: Path, runs_dir: Path) -> int:
    """Run upstream log_results.py.

    Returns 0 on real success. Returns non-zero when the script no-ops because
    ``mlflow`` is missing (upstream often exits 0 with a message — treat as
    failure so we can fall back to the minimal logger).
    """
    env = os.environ.copy()
    env["AGENT_EVAL_RUNS_DIR"] = str(runs_dir)
    cmd = [
        sys.executable,
        str(script),
        "--run-id",
        run_id,
        "--config",
        str(config),
    ]
    logger.info("Running AEH MLflow logger: %s", " ".join(cmd))
    result = subprocess.run(cmd, env=env, check=False, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    combined = f"{result.stdout}\n{result.stderr}"
    if _upstream_noop_missing_mlflow(combined):
        logger.warning("AEH log_results.py skipped (mlflow package missing)")
        return 2
    return result.returncode


def _do_log_results(
    *,
    run_id: str,
    config: Path,
    runs_dir: Path,
    tracking_uri: str,
    experiment: str | None,
) -> int:
    """Log results via AEH script or minimal fallback. Returns 0 on success."""
    script = _resolve_log_results_script()
    if script is not None:
        rc = _run_aeh_log_results(
            script=script,
            run_id=run_id,
            config=config,
            runs_dir=runs_dir,
        )
        if rc == 0:
            return 0
        logger.warning("AEH log_results.py exited %s; trying minimal logger", rc)

    try:
        _minimal_mlflow_log(
            run_id=run_id,
            config=config,
            runs_dir=runs_dir,
            tracking_uri=tracking_uri,
            experiment=experiment,
        )
    except Exception:
        logger.exception("MLflow logging failed")
        return 1
    return 0


def _minimal_mlflow_log(
    *,
    run_id: str,
    config: Path,
    runs_dir: Path,
    tracking_uri: str,
    experiment: str | None = None,
) -> None:
    try:
        import mlflow
    except ImportError as exc:
        raise RuntimeError("mlflow is not installed; pip install 'mlflow[genai]' or use AEH image with mlflow") from exc

    raw = yaml.safe_load(config.read_text()) or {}
    skill = str(raw.get("skill") or config.parent.name)
    mlflow_cfg = raw.get("mlflow") if isinstance(raw.get("mlflow"), dict) else {}
    exp_name = (experiment or "").strip() or str(mlflow_cfg.get("experiment") or skill or "abevalflow-aeh")

    run_dir = _resolve_run_dir(runs_dir, skill, run_id)

    run_result: dict = {}
    rr_path = run_dir / "run_result.json"
    if rr_path.is_file():
        run_result = json.loads(rr_path.read_text()) or {}

    summary: dict = {}
    summary_path = run_dir / "summary.yaml"
    if summary_path.is_file():
        loaded = yaml.safe_load(summary_path.read_text()) or {}
        if isinstance(loaded, dict):
            summary = loaded

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(exp_name)
    with mlflow.start_run(run_name=run_id):
        mlflow.log_param("skill", skill)
        mlflow.log_param("run_id", run_id)
        mlflow.log_param("pipeline_experiment", exp_name)
        mlflow.log_param("execution_mode", run_result.get("execution_mode", "harbor"))
        variant = _variant_from_run_id(run_id)
        if variant:
            mlflow.log_param("variant", variant)
        if run_result.get("model"):
            mlflow.log_param("model", run_result["model"])

        tokens = run_result.get("token_usage") or {}
        if isinstance(tokens, dict):
            for key in ("input", "output", "prompt", "completion", "total"):
                if key in tokens and tokens[key] is not None:
                    mlflow.log_metric(f"tokens_{key}", float(tokens[key]))
            # Harbor-style nested usage
            for key in ("input_tokens", "output_tokens", "cache_read", "cache_create"):
                if key in tokens and tokens[key] is not None:
                    mlflow.log_metric(f"tokens_{key}", float(tokens[key]))

        if run_result.get("cost_usd") is not None:
            mlflow.log_metric("cost_usd", float(run_result["cost_usd"]))
        mean_reward = run_result.get("mean_reward")
        if mean_reward is None:
            mean_reward = summary.get("mean_reward")
        if mean_reward is None:
            try:
                from scripts.aggregate_aeh import _extract_mean_reward

                mean_reward = _extract_mean_reward(run_dir)
            except Exception:
                logger.warning("Could not derive mean_reward for MLflow", exc_info=True)
                mean_reward = None
        if mean_reward is not None:
            mlflow.log_metric("mean_reward", float(mean_reward))

        for artifact in (summary_path, rr_path, run_dir / "report.html"):
            if not artifact.is_file():
                continue
            try:
                mlflow.log_artifact(str(artifact))
            except Exception:
                # Metrics/params already recorded; artifacts need a proxied
                # artifact root (mlflow-artifacts:/) on the tracking server.
                logger.warning("MLflow artifact upload failed for %s", artifact, exc_info=True)

    logger.info("Minimal MLflow log complete: experiment=%s run_id=%s", exp_name, run_id)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Log AEH results to MLflow")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--runs-dir", type=Path, required=True)
    parser.add_argument("--tracking-uri", default=os.environ.get("MLFLOW_TRACKING_URI", ""))
    parser.add_argument(
        "--experiment",
        default="",
        help=(
            "MLflow experiment name for this PipelineRun. When set, all variants "
            "(control/treatment) share this one experiment. Prefer the Tekton "
            "PipelineRun name."
        ),
    )
    parser.add_argument(
        "--enabled",
        default="true",
        help="Set to false/0/no to skip (default: true when URI set by caller)",
    )
    parser.add_argument(
        "--actions",
        default=",".join(_DEFAULT_ACTIONS),
        help=(
            f"Comma-separated actions: log-results, push-feedback, sync-dataset (default: {','.join(_DEFAULT_ACTIONS)})"
        ),
    )
    args = parser.parse_args(argv)

    enabled = str(args.enabled).strip().lower() in {"1", "true", "yes", "on"}
    tracking_uri = (args.tracking_uri or "").strip()
    if not enabled:
        logger.info("MLflow logging disabled (--enabled=%s)", args.enabled)
        return 0
    if not tracking_uri:
        logger.info("MLflow logging skipped (empty tracking URI)")
        return 0

    os.environ["MLFLOW_TRACKING_URI"] = tracking_uri
    config = args.config.resolve()
    runs_dir = args.runs_dir.resolve()
    if not config.is_file():
        logger.error("Config not found: %s", config)
        return 1

    if not _mlflow_importable():
        logger.error(
            "Python package 'mlflow' is not installed in this environment; install with: python -m pip install mlflow"
        )
        return 1

    actions = _parse_actions(args.actions)
    logger.info("MLflow actions: %s", ", ".join(actions))

    experiment = (args.experiment or "").strip()
    raw_cfg = yaml.safe_load(config.read_text()) or {}
    config_skill = (
        _config_reports_leaf(raw_cfg, config.parent.name)
        if isinstance(raw_cfg, dict)
        else config.parent.name
    )
    reports_skill = _reports_skill_name(runs_dir, config_skill, args.run_id)

    patched_config: Path | None = None
    effective_config = config
    if experiment:
        patched_config = _write_patched_config(
            config,
            experiment=experiment,
            reports_skill=reports_skill,
            tracking_uri=tracking_uri,
        )
        effective_config = patched_config
        logger.info(
            "One experiment per PipelineRun: experiment=%s reports_skill=%s run_id=%s",
            experiment,
            reports_skill,
            args.run_id,
        )

    try:
        # Belt-and-suspenders: AEH log_results rejects absolute harbor_job_dir.
        try:
            from abevalflow.harbor_extensions.aeh_paths import rewrite_harbor_job_dir

            try:
                run_dir = _resolve_run_dir(runs_dir, reports_skill, args.run_id)
            except FileNotFoundError:
                run_dir = None
            if run_dir is not None:
                rr = run_dir / "run_result.json"
                if rr.is_file():
                    rewritten = rewrite_harbor_job_dir(rr)
                    if rewritten:
                        logger.info("Rewrote harbor_job_dir -> %s", rewritten)
        except Exception:
            logger.warning("harbor_job_dir rewrite failed; continuing", exc_info=True)

        # sync-dataset resolves dataset.path relative to config.parent — keep a
        # patch beside the submission when overriding experiment.
        if "sync-dataset" in actions:
            sync_config: Path | None = None
            try:
                if experiment:
                    sync_config = _write_submission_local_patch(
                        config,
                        experiment=experiment,
                        reports_skill=reports_skill,
                        tracking_uri=tracking_uri,
                    )
                else:
                    sync_config = config
                _maybe_sync_dataset(config=sync_config, runs_dir=runs_dir)
            except Exception:
                logger.warning("sync-dataset failed (non-blocking)", exc_info=True)
            finally:
                if sync_config is not None and sync_config != config:
                    sync_config.unlink(missing_ok=True)

        log_rc = 0
        if "log-results" in actions:
            log_rc = _do_log_results(
                run_id=args.run_id,
                config=effective_config,
                runs_dir=runs_dir,
                tracking_uri=tracking_uri,
                experiment=experiment or None,
            )

        if "push-feedback" in actions:
            try:
                _maybe_push_feedback(run_id=args.run_id, config=effective_config, runs_dir=runs_dir)
            except Exception:
                logger.warning("push-feedback failed (non-blocking)", exc_info=True)

        # Keep historical behavior: non-zero only when log-results itself fails.
        return log_rc
    finally:
        if patched_config is not None:
            patched_config.unlink(missing_ok=True)


def _write_submission_local_patch(
    config: Path,
    *,
    experiment: str,
    reports_skill: str,
    tracking_uri: str = "",
) -> Path:
    """Patch next to the submission so dataset.path stays resolvable."""
    raw = yaml.safe_load(config.read_text()) or {}
    if not isinstance(raw, dict):
        raw = {}
    raw = _apply_mlflow_eval_patch(
        raw,
        experiment=experiment,
        reports_skill=reports_skill,
        tracking_uri=tracking_uri,
    )
    path = config.parent / f".abevalflow-mlflow-{os.getpid()}.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
    return path


if __name__ == "__main__":
    raise SystemExit(main())
