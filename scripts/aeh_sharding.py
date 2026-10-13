#!/usr/bin/env python3
"""Shard AEH OpenShell single runs across concurrent harness subprocesses.

The harness executes a run's cases sequentially inside one
``agent_eval.openshell.run`` process. This module fans a single run out into
N shard subprocesses and merges their run directories back into one, so the
pipeline's evaluate step (aggregate_aeh.py, MLflow logging, the regression
check) sees exactly the artifacts a sequential run would have produced.

Sharding contract:

- A shard config is a sibling of the (already judge-patched) eval.yaml with
  only ``dataset.path`` repointed at a directory holding that shard's case
  directories. Cases are copied, not symlinked: os.walk-based dataset
  loaders do not follow directory symlinks.
- Shard i runs with ``--run-id <rid>-s<i>`` under the same reports leaf, so
  shard run directories never collide with the canonical ``<rid>`` one.
- ``merge_run_dirs`` writes the canonical run dir after every shard
  succeeded: per_case/judges merged, mean_reward recomputed with the same
  case-reward logic aggregate_aeh.py uses, wall clock = max over shards,
  cost/tokens summed, per-case artifacts and stdout.log concatenated.
- Any shard failure or missing summary.yaml aborts the merge — a green
  merged run always means every case ran and was judged.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

try:  # repo root on sys.path (tests, pipeline PYTHONPATH)
    from scripts.aggregate_aeh import _mean_reward_from_per_case
except ImportError:  # running as a script from scripts/
    from aggregate_aeh import _mean_reward_from_per_case  # type: ignore[no-redef]


class ShardMergeError(Exception):
    """Raised when shard outputs cannot be merged into one run."""


def list_case_dirs(cases_root: Path) -> list[Path]:
    """Case directories under a dataset root: subdirs holding input.yaml."""
    if not cases_root.is_dir():
        return []
    return sorted(
        (d for d in cases_root.iterdir() if d.is_dir() and (d / "input.yaml").is_file()),
        key=lambda d: d.name,
    )


def shard_cases(cases: list[Path], n_shards: int) -> list[list[Path]]:
    """Round-robin split so long and short cases spread evenly across shards."""
    n = max(1, min(n_shards, len(cases)))
    shards: list[list[Path]] = [[] for _ in range(n)]
    for i, case in enumerate(cases):
        shards[i % n].append(case)
    return shards


def dataset_root(config: Path) -> Path | None:
    """Resolve ``dataset.path`` against the config's directory."""
    try:
        raw = yaml.safe_load(config.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(raw, dict):
        return None
    dataset = raw.get("dataset")
    if not isinstance(dataset, dict) or not dataset.get("path"):
        return None
    path = Path(str(dataset["path"]))
    if not path.is_absolute():
        path = config.parent / path
    return path


def write_shard_configs(
    config: Path,
    shards: list[list[Path]],
    *,
    tag: str,
) -> tuple[list[Path], Path]:
    """Write one eval.yaml sibling per shard with ``dataset.path`` repointed.

    The shard configs stay in the original config's directory so every other
    relative path in the config (skills, outputs, workspace) resolves as
    before. Case directories are copied into ``.aeh-shards-<tag>/shard-<i>/``.

    Returns:
        (shard config paths, shard root) for ``cleanup_shard_files``.
    """
    root = config.parent / f".aeh-shards-{tag}"
    raw = yaml.safe_load(config.read_text()) or {}
    if not isinstance(raw, dict):
        raise ShardMergeError(f"{config} is not a mapping")
    dataset = raw.get("dataset")
    if not isinstance(dataset, dict) or not dataset.get("path"):
        raise ShardMergeError(f"{config} has no dataset.path to repoint")
    relative = not Path(str(dataset["path"])).is_absolute()

    configs: list[Path] = []
    for i, cases in enumerate(shards):
        shard_root = root / f"shard-{i}"
        shard_root.mkdir(parents=True, exist_ok=True)
        for case in cases:
            shutil.copytree(case, shard_root / case.name, dirs_exist_ok=True)
        shard_path = Path(f".aeh-shards-{tag}/shard-{i}") if relative else shard_root
        cfg = dict(raw)
        cfg["dataset"] = {**dataset, "path": str(shard_path)}
        path = config.parent / f".eval.shard-{i}.{tag}.yaml"
        path.write_text(yaml.safe_dump(cfg, sort_keys=False))
        configs.append(path)
    return configs, root


def cleanup_shard_files(configs: list[Path], root: Path) -> None:
    """Remove shard configs and copied case directories."""
    for path in configs:
        path.unlink(missing_ok=True)
    shutil.rmtree(root, ignore_errors=True)


def _judge_value(record: Any) -> tuple[Any, bool]:
    """(value, errored) for one per_case judge record."""
    if isinstance(record, dict):
        if record.get("error") and record.get("value") is None:
            return None, True
        return record.get("value"), False
    return record, False


def merge_judges(per_case: dict[str, Any]) -> dict[str, Any]:
    """Recompute per-judge aggregates over merged per_case results.

    Numeric (Likert) judges get a mean, boolean judges a pass_rate, plus an
    errored_cases count when any case's judge errored — matching the fields
    aggregate_aeh.py and the engines read from summary.yaml judges blocks.
    """
    agg: dict[str, dict[str, Any]] = {}
    for case_data in per_case.values():
        if not isinstance(case_data, dict):
            continue
        for name, record in case_data.items():
            if name == "reward":
                continue
            entry = agg.setdefault(name, {"numeric": [], "bools": [], "errored": 0})
            value, errored = _judge_value(record)
            if errored:
                entry["errored"] += 1
            elif isinstance(value, bool):
                entry["bools"].append(value)
            elif isinstance(value, (int, float)):
                entry["numeric"].append(value)

    judges: dict[str, Any] = {}
    for name, entry in agg.items():
        out: dict[str, Any] = {}
        if entry["numeric"]:
            out["mean"] = sum(entry["numeric"]) / len(entry["numeric"])
        if entry["bools"]:
            out["pass_rate"] = sum(entry["bools"]) / len(entry["bools"])
        if entry["errored"]:
            out["errored_cases"] = entry["errored"]
        if out:
            judges[name] = out
    return judges


def _numeric(values: list[Any]) -> list[float]:
    return [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]


def _max_optional(values: list[Any]) -> float | None:
    nums = _numeric(values)
    return max(nums) if nums else None


def _sum_optional(values: list[Any]) -> float | None:
    nums = _numeric(values)
    return sum(nums) if nums else None


def merge_token_usage(usages: list[Any]) -> Any:
    """Sum token_usage across shards: plain totals or per-key dicts."""
    nums = _numeric(usages)
    dicts = [u for u in usages if isinstance(u, dict)]
    if nums and not dicts:
        return sum(nums)
    if dicts:
        merged: dict[str, Any] = {}
        for usage in dicts:
            for key, val in usage.items():
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    merged[key] = merged.get(key, 0) + val
                else:
                    merged.setdefault(key, val)
        return merged
    return None


def merge_run_dirs(shard_dirs: list[Path], dest: Path, *, run_id: str) -> dict[str, Any]:
    """Merge shard run directories into one canonical run directory.

    Every shard must have produced a summary.yaml (a shard that failed or
    produced nothing aborts the merge — the caller reports the failure and
    keeps the shard dirs). Case ids must be disjoint. The merged summary.yaml
    and run_result.json carry the same fields a sequential run would, so
    aggregate_aeh.py, MLflow logging and the regression check work unchanged.

    Returns:
        The merged summary dict.
    """
    summaries: list[dict[str, Any]] = []
    run_results: list[dict[str, Any]] = []
    for shard_dir in shard_dirs:
        summary_path = shard_dir / "summary.yaml"
        if not summary_path.is_file():
            raise ShardMergeError(f"shard run dir {shard_dir} has no summary.yaml")
        try:
            summary = yaml.safe_load(summary_path.read_text()) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ShardMergeError(f"unreadable summary.yaml in {shard_dir}: {exc}") from exc
        if not isinstance(summary, dict):
            raise ShardMergeError(f"summary.yaml in {shard_dir} is not a mapping")
        summaries.append(summary)

        run_result: dict[str, Any] = {}
        rr_path = shard_dir / "run_result.json"
        if rr_path.is_file():
            try:
                loaded = json.loads(rr_path.read_text())
            except (OSError, json.JSONDecodeError) as exc:
                raise ShardMergeError(f"unreadable run_result.json in {shard_dir}: {exc}") from exc
            if not isinstance(loaded, dict):
                raise ShardMergeError(f"run_result.json in {shard_dir} is not a mapping")
            run_result = loaded
        run_results.append(run_result)

    per_case: dict[str, Any] = {}
    for summary in summaries:
        for case_id, case_data in (summary.get("per_case") or {}).items():
            if case_id in per_case:
                raise ShardMergeError(f"case '{case_id}' appears in more than one shard")
            per_case[case_id] = case_data

    mean_reward = _mean_reward_from_per_case(per_case)

    merged_summary = dict(summaries[0])
    merged_summary["run_id"] = run_id
    merged_summary["per_case"] = per_case
    merged_summary["judges"] = merge_judges(per_case)
    merged_summary["mean_reward"] = mean_reward
    merged_summary.pop("pairwise", None)

    # Start from shard 0's run_result for engine-specific passthrough fields
    # (execution_mode, agent, ...), then replace every shard-scoped value.
    merged_rr = dict(run_results[0])
    for key in (
        "mean_reward",
        "n_cases",
        "num_cases",
        "n_failed",
        "duration_s",
        "wall_clock_s",
        "cost_usd",
        "token_usage",
    ):
        merged_rr.pop(key, None)
    merged_rr["mean_reward"] = mean_reward
    merged_rr["n_cases"] = len(per_case)
    merged_rr["num_cases"] = len(per_case)
    declared_failed = [rr.get("n_failed") for rr in run_results if isinstance(rr.get("n_failed"), int)]
    if declared_failed:
        merged_rr["n_failed"] = sum(declared_failed)
    # Shards run in parallel: wall clock is the longest shard, not the sum.
    for key, value in (
        ("duration_s", _max_optional([rr.get("duration_s") for rr in run_results])),
        ("wall_clock_s", _max_optional([rr.get("wall_clock_s") for rr in run_results])),
        ("cost_usd", _sum_optional([rr.get("cost_usd") for rr in run_results])),
    ):
        if value is not None:
            merged_rr[key] = value
    token_usage = merge_token_usage([rr.get("token_usage") for rr in run_results])
    if token_usage is not None:
        merged_rr["token_usage"] = token_usage

    dest.mkdir(parents=True, exist_ok=True)
    (dest / "summary.yaml").write_text(yaml.safe_dump(merged_summary, sort_keys=False))
    (dest / "run_result.json").write_text(json.dumps(merged_rr, indent=2))

    for shard_dir in shard_dirs:
        cases_dir = shard_dir / "cases"
        if not cases_dir.is_dir():
            continue
        for case_dir in cases_dir.iterdir():
            target = dest / "cases" / case_dir.name
            if target.exists():
                raise ShardMergeError(f"case artifacts already present: {target}")
            shutil.copytree(case_dir, target)

    logs = [d / "stdout.log" for d in shard_dirs if (d / "stdout.log").is_file()]
    if logs:
        with open(dest / "stdout.log", "wb") as out:
            for log in logs:
                out.write(log.read_bytes())

    return merged_summary
