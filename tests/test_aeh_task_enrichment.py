"""Tests for AEH Harbor task enrichment (skills + annotations)."""

from __future__ import annotations

from pathlib import Path

import yaml

from abevalflow.harbor_extensions.aeh_task_enrichment import enrich_harbor_tasks


def _write_generated_task(task_dir: Path, *, workdir: str = "/workspace") -> None:
    (task_dir / "environment").mkdir(parents=True)
    (task_dir / "tests").mkdir(parents=True)
    (task_dir / "task.toml").write_text(f'[environment]\ndocker_image = "img:latest"\nworkdir = "{workdir}"\n')
    (task_dir / "tests" / "eval.yaml").write_text(
        yaml.safe_dump(
            {
                "dataset": {"path": "", "schema": "x"},
                "judges": [{"name": "file_created", "check": "return True, 'ok'"}],
            },
            sort_keys=False,
        )
    )
    (task_dir / "tests" / "test.sh").write_text(
        f"""#!/bin/bash
set -o pipefail
mkdir -p /logs/verifier
python3 -m agent_eval.harbor.reward \\
  --config /tests/eval.yaml \\
  --case-dir "{workdir}" \\
  --out-dir /logs/verifier
"""
    )
    (task_dir / "tests" / "test.sh").chmod(0o755)


def test_enrich_copies_annotations_and_restores_dataset_path(tmp_path: Path):
    submission = tmp_path / "submission"
    cases = submission / "cases" / "case-001"
    cases.mkdir(parents=True)
    (cases / "annotations.yaml").write_text(
        yaml.safe_dump({"expected_file": "greeting.txt", "expected_content": "Hello\n" * 100})
    )
    (cases / "greeting.txt").write_text("ground truth")
    (submission / "eval.yaml").write_text(
        yaml.safe_dump(
            {
                "skill": "demo",
                "dataset": {"path": "cases"},
                "runner": {"type": "claude-code"},
                "judges": [{"name": "file_created"}],
            }
        )
    )

    tasks = tmp_path / "tasks"
    task = tasks / "case-001"
    _write_generated_task(task)

    n = enrich_harbor_tasks(tasks, config_path=submission / "eval.yaml")
    assert n == 1

    ann = task / "tests" / "cases" / "workspace" / "annotations.yaml"
    assert ann.is_file()
    assert yaml.safe_load(ann.read_text())["expected_file"] == "greeting.txt"
    assert (ann.parent / "greeting.txt").read_text() == "ground truth"

    bundled = yaml.safe_load((task / "tests" / "eval.yaml").read_text())
    assert bundled["dataset"]["path"] == "cases"

    test_sh = (task / "tests" / "test.sh").read_text()
    assert "Agentic Eval Flow: stage annotations" not in test_sh
    assert "python3 -m agent_eval.harbor.reward" in test_sh
    assert not (task / "environment" / "annotations.yaml").exists()


def test_enrich_copies_skills_and_sets_skills_dir(tmp_path: Path):
    submission = tmp_path / "submission"
    skill_dir = submission / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\n# demo\n")
    (submission / "cases" / "case-001").mkdir(parents=True)
    (submission / "eval.yaml").write_text(
        yaml.safe_dump(
            {
                "skill": "demo-skill",
                "dataset": {"path": "cases"},
                "runner": {"type": "claude-code", "plugin_dirs": ["skills"]},
                "judges": [{"name": "exit_success"}],
            }
        )
    )

    tasks = tmp_path / "tasks"
    task = tasks / "case-001"
    _write_generated_task(task)

    enrich_harbor_tasks(tasks, config_path=submission / "eval.yaml")

    assert (task / "environment" / "skills" / "demo-skill" / "SKILL.md").is_file()
    toml = (task / "task.toml").read_text()
    assert 'skills_dir = "/workspace/skills"' in toml


def test_enrich_copies_skills_from_pack_root_skills_subdir(tmp_path: Path):
    """plugin_dirs pointing at a pack ROOT (e.g. ["../../../rh-sre"]), not
    directly at its skills/ folder -- the convention every agentic-plugins
    submission uses so the SAME plugin_dirs entry also resolves the pack's
    mcps.json for the mock-MCP swap step (see aeh-skill-eval.yaml). The real
    skill lives two levels down: <plugin_dirs entry>/skills/<name>/SKILL.md.
    Mirrors scripts/validate.py's _check_aeh_plugin_dirs 'skills_sub' case.
    """
    submission = tmp_path / "submissions" / "rh-sre" / "cve-validation"
    pack_root = tmp_path / "rh-sre"
    skill_dir = pack_root / "skills" / "cve-validation"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: cve-validation\n---\n# cve-validation\n")
    (pack_root / "mcps.json").write_text("{}")
    (submission / "cases" / "case-001").mkdir(parents=True)
    (submission / "eval.yaml").write_text(
        yaml.safe_dump(
            {
                "skill": "cve-validation",
                "dataset": {"path": "cases"},
                "runner": {"type": "claude-code", "plugin_dirs": ["../../../rh-sre"]},
                "judges": [{"name": "exit_success"}],
            }
        )
    )

    tasks = tmp_path / "tasks"
    task = tasks / "case-001"
    _write_generated_task(task)

    enrich_harbor_tasks(tasks, config_path=submission / "eval.yaml")

    # Copied environment/skills/cve-validation/SKILL.md -- NOT
    # environment/skills/skills/cve-validation/SKILL.md (no doubled "skills/"
    # level) -- and the sibling mcps.json / other pack dirs are not dragged
    # along into the Harbor skills_dir scan.
    assert (task / "environment" / "skills" / "cve-validation" / "SKILL.md").is_file()
    assert not (task / "environment" / "skills" / "skills").exists()
    assert not (task / "environment" / "skills" / "mcps.json").exists()
    toml = (task / "task.toml").read_text()
    assert 'skills_dir = "/workspace/skills"' in toml


def test_enrich_preserves_tool_interception_artifacts(tmp_path: Path):
    submission = tmp_path / "submission"
    skill_dir = submission / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo-skill\n---\n# demo\n")
    (submission / "cases" / "case-001").mkdir(parents=True)
    (submission / "eval.yaml").write_text(
        yaml.safe_dump(
            {
                "skill": "demo-skill",
                "dataset": {"path": "cases"},
                "runner": {"type": "claude-code", "plugin_dirs": ["skills"]},
                "inputs": {"tools": [{"match": "Block MCP tools", "prompt": "deny"}]},
                "permissions": {"deny": ["mcp__*"]},
                "judges": [{"name": "exit_success"}],
            }
        )
    )
    (submission / "tool_handlers.yaml").write_text(
        yaml.safe_dump({"handlers": [{"patterns": ["mcp__*"], "action": "deny"}]})
    )

    tasks = tmp_path / "tasks"
    task = tasks / "case-001"
    _write_generated_task(task)
    env = task / "environment"
    (env / "hooks").mkdir()
    (env / "hooks" / "tools.py").write_text("# interceptor\n")
    (env / ".claude").mkdir()
    (env / ".claude" / "settings.json").write_text('{"hooks":{}}')
    (env / "tool_handlers.yaml").write_text("handlers: []\n")

    enrich_harbor_tasks(tasks, config_path=submission / "eval.yaml")

    assert (env / "hooks" / "tools.py").read_text() == "# interceptor\n"
    assert (env / "tool_handlers.yaml").is_file()
    assert (env / ".claude" / "settings.json").is_file()
    assert (env / "skills" / "demo-skill" / "SKILL.md").is_file()


def test_enrich_copies_submission_tool_handlers_when_missing(tmp_path: Path):
    submission = tmp_path / "submission"
    (submission / "cases" / "case-001").mkdir(parents=True)
    (submission / "eval.yaml").write_text(
        yaml.safe_dump(
            {
                "skill": "demo",
                "dataset": {"path": "cases"},
                "inputs": {"tools": [{"match": "Ask user questions"}]},
                "judges": [{"name": "exit_success"}],
            }
        )
    )
    (submission / "tool_handlers.yaml").write_text("handlers:\n  - patterns: [AskUserQuestion]\n")

    tasks = tmp_path / "tasks"
    task = tasks / "case-001"
    _write_generated_task(task)

    enrich_harbor_tasks(tasks, config_path=submission / "eval.yaml")
    assert (task / "environment" / "tool_handlers.yaml").is_file()
    assert "AskUserQuestion" in (task / "environment" / "tool_handlers.yaml").read_text()


def test_enrich_patches_models_and_injects_weighted_reward(tmp_path: Path):
    submission = tmp_path / "submission"
    (submission / "cases" / "case-001").mkdir(parents=True)
    (submission / "eval.yaml").write_text(
        yaml.safe_dump(
            {
                "skill": "demo",
                "dataset": {"path": "cases"},
                "models": {"skill": "old-skill", "judge": "old-judge"},
                "judges": [
                    {"name": "correctness", "type": "llm"},
                    {"name": "helpfulness", "type": "llm"},
                ],
            }
        )
    )

    tasks = tmp_path / "tasks"
    task = tasks / "case-001"
    _write_generated_task(task)
    (task / "tests" / "eval.yaml").write_text(
        yaml.safe_dump(
            {
                "dataset": {"path": ""},
                "models": {"skill": "old-skill", "judge": "old-judge"},
                "judges": [
                    {"name": "correctness", "type": "llm"},
                    {"name": "helpfulness", "type": "llm"},
                ],
            },
            sort_keys=False,
        )
    )

    enrich_harbor_tasks(
        tasks,
        config_path=submission / "eval.yaml",
        skill_model="claude-sonnet",
        judge_model="claude-sonnet",
    )

    bundled = yaml.safe_load((task / "tests" / "eval.yaml").read_text())
    assert bundled["models"]["skill"] == "claude-sonnet"
    assert bundled["models"]["judge"] == "claude-sonnet"
    assert bundled["reward"]["formula"] == "weighted"
    assert bundled["reward"]["score_range"] == [0, 1]
    assert bundled["reward"]["weights"] == {
        "correctness": 1.0,
        "helpfulness": 1.0,
    }


def test_enrich_preserves_submission_reward_without_score_range(tmp_path: Path):
    """Likert submissions may omit score_range (AEH default [1,5]); do not force [0,1]."""
    submission = tmp_path / "submission"
    (submission / "cases" / "case-001").mkdir(parents=True)
    (submission / "eval.yaml").write_text(
        yaml.safe_dump(
            {
                "skill": "demo",
                "dataset": {"path": "cases"},
                "reward": {
                    "formula": "weighted",
                    "weights": {"correctness": 1.0, "helpfulness": 1.0},
                },
                "judges": [{"name": "correctness"}, {"name": "helpfulness"}],
            }
        )
    )

    tasks = tmp_path / "tasks"
    task = tasks / "case-001"
    _write_generated_task(task)
    (task / "tests" / "eval.yaml").write_text(
        yaml.safe_dump(
            {
                "dataset": {"path": ""},
                "reward": {
                    "formula": "weighted",
                    "weights": {"correctness": 1.0, "helpfulness": 1.0},
                },
                "judges": [{"name": "correctness"}, {"name": "helpfulness"}],
            },
            sort_keys=False,
        )
    )

    enrich_harbor_tasks(tasks, config_path=submission / "eval.yaml")
    bundled = yaml.safe_load((task / "tests" / "eval.yaml").read_text())
    assert "score_range" not in bundled["reward"]
    assert bundled["reward"]["weights"] == {
        "correctness": 1.0,
        "helpfulness": 1.0,
    }


def test_enrich_preserves_explicit_reward_section(tmp_path: Path):
    submission = tmp_path / "submission"
    (submission / "cases" / "case-001").mkdir(parents=True)
    (submission / "eval.yaml").write_text(
        yaml.safe_dump(
            {
                "skill": "demo",
                "dataset": {"path": "cases"},
                "reward": {"formula": "weighted", "weights": {"correctness": 2.0}},
                "judges": [{"name": "correctness"}, {"name": "helpfulness"}],
            }
        )
    )

    tasks = tmp_path / "tasks"
    task = tasks / "case-001"
    _write_generated_task(task)
    (task / "tests" / "eval.yaml").write_text(
        yaml.safe_dump(
            {
                "dataset": {"path": ""},
                "reward": {"formula": "weighted", "weights": {"correctness": 2.0}},
                "judges": [{"name": "correctness"}, {"name": "helpfulness"}],
            },
            sort_keys=False,
        )
    )

    enrich_harbor_tasks(tasks, config_path=submission / "eval.yaml")
    bundled = yaml.safe_load((task / "tests" / "eval.yaml").read_text())
    assert bundled["reward"]["weights"] == {"correctness": 2.0}
