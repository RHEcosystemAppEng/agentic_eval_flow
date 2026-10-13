"""Tests for scripts/aeh_sharding.py - concurrent OpenShell case shards."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts.aeh_sharding import (
    ShardMergeError,
    cleanup_shard_files,
    dataset_root,
    list_case_dirs,
    merge_judges,
    merge_run_dirs,
    merge_token_usage,
    shard_cases,
    write_shard_configs,
)


def _make_case(root: Path, name: str) -> Path:
    case = root / name
    case.mkdir(parents=True)
    (case / "input.yaml").write_text("prompt: hi\n")
    (case / "annotations.yaml").write_text("expected: []\n")
    return case


def _make_submission(root: Path, n_cases: int, **config_extra: object) -> Path:
    """eval.yaml + cases; returns the config path."""
    config = {
        "name": "forge-eval-rubrics",
        "dataset": {"path": "cases"},
    }
    config.update(config_extra)
    cases = root / "cases"
    for i in range(n_cases):
        _make_case(cases, f"case-{i:03d}")
    eval_path = root / "eval.yaml"
    eval_path.write_text(yaml.safe_dump(config, sort_keys=False))
    return eval_path


def _make_shard_run(
    run_dir: Path,
    run_id: str,
    per_case: dict,
    *,
    duration_s: float = 10.0,
    cost_usd: float = 1.0,
    token_usage: dict | None = None,
    n_failed: int | None = None,
    cases: dict[str, str] | None = None,
    stdout_log: str | None = None,
) -> Path:
    """Write a harness-shaped shard run dir."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.yaml").write_text(
        yaml.safe_dump(
            {"run_id": run_id, "per_case": per_case, "judges": {}},
            sort_keys=False,
        )
    )
    run_result: dict = {"run_id": run_id, "duration_s": duration_s, "cost_usd": cost_usd}
    if token_usage is not None:
        run_result["token_usage"] = token_usage
    if n_failed is not None:
        run_result["n_failed"] = n_failed
    (run_dir / "run_result.json").write_text(json.dumps(run_result))
    for case_id, payload in (cases or {}).items():
        artifact = run_dir / "cases" / case_id / "output"
        artifact.mkdir(parents=True)
        (artifact / "response.txt").write_text(payload)
    if stdout_log is not None:
        (run_dir / "stdout.log").write_text(stdout_log)
    return run_dir


class TestListCaseDirs:
    def test_only_dirs_with_input_yaml(self, tmp_path: Path):
        _make_case(tmp_path, "case-a")
        _make_case(tmp_path, "case-b")
        (tmp_path / "not-a-case").mkdir()
        (tmp_path / "loose-file.yaml").write_text("x: 1\n")
        names = [d.name for d in list_case_dirs(tmp_path)]
        assert names == ["case-a", "case-b"]

    def test_missing_root(self, tmp_path: Path):
        assert list_case_dirs(tmp_path / "nope") == []


class TestShardCases:
    def test_round_robin(self, tmp_path: Path):
        cases = [_make_case(tmp_path, f"c{i}") for i in range(4)]
        shards = shard_cases(cases, 2)
        assert [c.name for c in shards[0]] == ["c0", "c2"]
        assert [c.name for c in shards[1]] == ["c1", "c3"]

    def test_capped_at_case_count(self, tmp_path: Path):
        cases = [_make_case(tmp_path, f"c{i}") for i in range(2)]
        shards = shard_cases(cases, 5)
        assert len(shards) == 2

    def test_single_shard(self, tmp_path: Path):
        cases = [_make_case(tmp_path, f"c{i}") for i in range(3)]
        shards = shard_cases(cases, 1)
        assert len(shards) == 1
        assert len(shards[0]) == 3


class TestDatasetRoot:
    def test_relative_resolved_against_config_dir(self, tmp_path: Path):
        config = _make_submission(tmp_path, 1)
        assert dataset_root(config) == tmp_path / "cases"

    def test_absolute_path_kept(self, tmp_path: Path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        config = tmp_path / "eval.yaml"
        config.write_text(yaml.safe_dump({"dataset": {"path": str(elsewhere)}}))
        assert dataset_root(config) == elsewhere

    def test_missing_dataset(self, tmp_path: Path):
        config = tmp_path / "eval.yaml"
        config.write_text("name: x\n")
        assert dataset_root(config) is None


class TestWriteShardConfigs:
    def test_configs_and_case_copies(self, tmp_path: Path):
        config = _make_submission(tmp_path, 2, models={"judge": "judge-glm-5-3"})
        cases = list_case_dirs(tmp_path / "cases")
        configs, root = write_shard_configs(config, [cases[:1], cases[1:]], tag="t1")

        assert [c.name for c in configs] == [".eval.shard-0.t1.yaml", ".eval.shard-1.t1.yaml"]
        for cfg in configs:
            assert cfg.parent == config.parent
        raw0 = yaml.safe_load(configs[0].read_text())
        # dataset.path repointed at the shard dir, relative to the config dir
        assert raw0["dataset"]["path"] == ".aeh-shards-t1/shard-0"
        assert (tmp_path / ".aeh-shards-t1" / "shard-0" / "case-000" / "input.yaml").is_file()
        assert (tmp_path / ".aeh-shards-t1" / "shard-1" / "case-001" / "input.yaml").is_file()
        # everything else passes through untouched
        assert raw0["name"] == "forge-eval-rubrics"
        assert raw0["models"] == {"judge": "judge-glm-5-3"}

        cleanup_shard_files(configs, root)
        assert not root.exists()
        assert all(not c.exists() for c in configs)

    def test_absolute_dataset_path_gets_absolute_shard_path(self, tmp_path: Path):
        cases_dir = tmp_path / "dataset"
        _make_case(cases_dir, "case-000")
        _make_case(cases_dir, "case-001")
        config = tmp_path / "eval.yaml"
        config.write_text(yaml.safe_dump({"dataset": {"path": str(cases_dir)}}))
        cases = list_case_dirs(cases_dir)
        configs, root = write_shard_configs(config, [cases], tag="t2")
        raw = yaml.safe_load(configs[0].read_text())
        assert Path(raw["dataset"]["path"]).is_absolute()
        assert raw["dataset"]["path"] == str(root / "shard-0")

    def test_config_without_dataset_raises(self, tmp_path: Path):
        config = tmp_path / "eval.yaml"
        config.write_text("name: x\n")
        try:
            write_shard_configs(config, [[]], tag="t3")
            assert False, "should have raised"
        except ShardMergeError as e:
            assert "dataset.path" in str(e)


class TestMergeJudges:
    def test_numeric_bool_and_errored(self):
        per_case = {
            "a": {"rubric": {"value": 5, "judge_type": "llm"}, "flag": {"value": True}},
            "b": {"rubric": {"value": 3, "judge_type": "llm"}, "flag": {"value": False}},
            "c": {"rubric": {"error": "boom"}, "flag": {"value": True}},
        }
        judges = merge_judges(per_case)
        assert judges["rubric"]["mean"] == 4.0
        assert judges["flag"]["pass_rate"] == 2 / 3
        assert judges["rubric"]["errored_cases"] == 1

    def test_reward_key_ignored(self):
        judges = merge_judges({"a": {"reward": 0.5}})
        assert judges == {}


class TestMergeTokenUsage:
    def test_dicts_summed_per_key(self):
        merged = merge_token_usage([{"input": 10, "output": 5}, {"input": 1, "output": 2}])
        assert merged == {"input": 11, "output": 7}

    def test_plain_totals_summed(self):
        assert merge_token_usage([10, 15]) == 25

    def test_absent(self):
        assert merge_token_usage([None, None]) is None


class TestMergeRunDirs:
    def _shards(self, tmp_path: Path):
        """Two shard dirs: [case-a(5), case-c(1)] and [case-b(3)]."""
        s0 = _make_shard_run(
            tmp_path / "runs" / "leaf" / "rid-s0",
            "rid-s0",
            {
                "case-a": {"rubric": {"value": 5, "judge_type": "llm"}},
                "case-c": {"rubric": {"value": 1, "judge_type": "llm"}},
            },
            duration_s=30.0,
            cost_usd=2.0,
            token_usage={"input": 10, "output": 5},
            n_failed=0,
            cases={"case-a": "briefing-a", "case-c": "briefing-c"},
            stdout_log="shard zero log\n",
        )
        s1 = _make_shard_run(
            tmp_path / "runs" / "leaf" / "rid-s1",
            "rid-s1",
            {"case-b": {"rubric": {"value": 3, "judge_type": "llm"}}},
            duration_s=20.0,
            cost_usd=1.0,
            token_usage={"input": 1, "output": 2},
            n_failed=1,
            cases={"case-b": "briefing-b"},
            stdout_log="shard one log\n",
        )
        return s0, s1

    def test_merge_produces_sequential_equivalent(self, tmp_path: Path):
        s0, s1 = self._shards(tmp_path)
        dest = tmp_path / "runs" / "leaf" / "rid"
        summary = merge_run_dirs([s0, s1], dest, run_id="rid")

        assert summary["run_id"] == "rid"
        assert set(summary["per_case"]) == {"case-a", "case-b", "case-c"}
        # Likert 5/3/1 normalize to rewards 1.0/0.5/0.0 → mean 0.5 over all
        # cases (the weighted-mean edge case has its own test below).
        assert summary["mean_reward"] == 0.5
        assert summary["judges"]["rubric"]["mean"] == 3.0
        assert "pairwise" not in summary

        loaded = yaml.safe_load((dest / "summary.yaml").read_text())
        assert loaded["per_case"] == summary["per_case"]
        rr = json.loads((dest / "run_result.json").read_text())
        assert rr["mean_reward"] == 0.5
        assert rr["n_cases"] == 3
        assert rr["num_cases"] == 3
        assert rr["n_failed"] == 1
        # shards run in parallel: wall clock is the longest shard
        assert rr["duration_s"] == 30.0
        assert rr["cost_usd"] == 3.0
        assert rr["token_usage"] == {"input": 11, "output": 7}

        for case in ("case-a", "case-b", "case-c"):
            assert (dest / "cases" / case / "output" / "response.txt").is_file()
        assert (dest / "stdout.log").read_text() == "shard zero log\nshard one log\n"

    def test_mean_reward_weighted_by_cases_not_shard_means(self, tmp_path: Path):
        # shard0: two cases scoring 5 and 1 (rewards 1.0, 0.0 → mean 0.5);
        # shard1: one case scoring 5 (reward 1.0 → mean 1.0). The true mean
        # over all three cases is 2/3; the average of the shard means would
        # be 0.75.
        s0 = _make_shard_run(
            tmp_path / "r" / "rid-s0",
            "rid-s0",
            {
                "case-a": {"rubric": {"value": 5}},
                "case-c": {"rubric": {"value": 1}},
            },
        )
        s1 = _make_shard_run(
            tmp_path / "r" / "rid-s1",
            "rid-s1",
            {"case-b": {"rubric": {"value": 5}}},
        )
        dest = tmp_path / "r" / "rid"
        summary = merge_run_dirs([s0, s1], dest, run_id="rid")
        assert summary["mean_reward"] == 2 / 3

    def test_duplicate_case_aborts(self, tmp_path: Path):
        per_case = {"case-a": {"rubric": {"value": 5}}}
        s0 = _make_shard_run(tmp_path / "r" / "rid-s0", "rid-s0", dict(per_case))
        s1 = _make_shard_run(tmp_path / "r" / "rid-s1", "rid-s1", dict(per_case))
        dest = tmp_path / "r" / "rid"
        try:
            merge_run_dirs([s0, s1], dest, run_id="rid")
            assert False, "should have raised"
        except ShardMergeError as e:
            assert "case-a" in str(e)
        # nothing partial written
        assert not (dest / "summary.yaml").exists()

    def test_missing_summary_aborts(self, tmp_path: Path):
        s0 = _make_shard_run(tmp_path / "r" / "rid-s0", "rid-s0", {})
        empty = tmp_path / "r" / "rid-s1"
        empty.mkdir(parents=True)
        dest = tmp_path / "r" / "rid"
        try:
            merge_run_dirs([s0, empty], dest, run_id="rid")
            assert False, "should have raised"
        except ShardMergeError as e:
            assert "summary.yaml" in str(e)
        assert not dest.exists()

    def test_run_result_without_n_failed_omits_it(self, tmp_path: Path):
        s0 = _make_shard_run(tmp_path / "r" / "rid-s0", "rid-s0", {"a": {"rubric": {"value": 5}}})
        s1 = _make_shard_run(tmp_path / "r" / "rid-s1", "rid-s1", {"b": {"rubric": {"value": 3}}})
        dest = tmp_path / "r" / "rid"
        merge_run_dirs([s0, s1], dest, run_id="rid")
        rr = json.loads((dest / "run_result.json").read_text())
        assert "n_failed" not in rr
