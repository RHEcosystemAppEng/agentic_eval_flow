"""AEH run naming, output discovery, and trial artifact collection."""

from __future__ import annotations

import shutil
from pathlib import Path

import yaml


def eval_dir_name(config_path: Path, fallback: str | None = None) -> str:
    """Resolve the AEH output leaf from execution.skill, skill, name, or an explicit fallback."""
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
