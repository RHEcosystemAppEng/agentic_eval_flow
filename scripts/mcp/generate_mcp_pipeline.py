#!/usr/bin/env python3
"""Generate the MCP Tekton Pipeline from phase definitions (no cluster changes).

Run from repo root:
    python -m scripts.mcp.generate_mcp_pipeline          # regenerate
    python -m scripts.mcp.generate_mcp_pipeline --check  # CI consistency check
    python -m scripts.mcp.generate_mcp_pipeline --task-files
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import yaml

from abevalflow.mcp.phase_config import PHASES

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / "pipeline/pipelines/mcp-eval-pipeline.template.yaml"
OUTPUT = REPO_ROOT / "pipeline/pipelines/mcp-eval-pipeline.yaml"


def task_files() -> list[Path]:
    files = []
    for phase in PHASES.values():
        task = phase.get("task")
        if task is None:
            raise ValueError(f"Missing Tekton task for phase {phase['id']}")
        file_path = (REPO_ROOT / task["file"]).resolve()
        if not file_path.is_relative_to(REPO_ROOT / "pipeline/tasks/konflux"):
            raise ValueError(f"Task path must be inside pipeline/tasks/konflux: {task['file']}")
        files.append(file_path)
    if len(files) != len(set(files)):
        raise ValueError("Duplicate Tekton task files in phase config")
    return files


def build_pipeline() -> dict:
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    spec = data["spec"]
    tasks = []
    seen_names = set()
    for phase in PHASES.values():
        task = phase["task"]
        if phase["id"] in seen_names:
            raise ValueError(f"Duplicate pipeline Task name: {phase['id']}")
        seen_names.add(phase["id"])
        pipeline_task = {
            "name": phase["id"],
            "onError": "continue",
            "taskRef": {
                "resolver": "bundles",
                "params": [
                    {"name": "bundle", "value": "$(params.task-bundle)"},
                    {"name": "name", "value": task["name"]},
                    {"name": "kind", "value": "Task"},
                ],
            },
            "params": [{"name": k, "value": v} for k, v in task.get("params", {}).items()],
            "workspaces": [{"name": k, "workspace": v} for k, v in task.get("workspaces", {}).items()],
        }
        if phase.get("depends_on"):
            pipeline_task["runAfter"] = phase["depends_on"]
        tasks.append(pipeline_task)
    spec["tasks"] = tasks
    return data


def verify_task_interfaces() -> None:
    """Catch config-vs-Task interface mismatches before publishing bundles."""
    for phase, path in zip(PHASES.values(), task_files()):
        if not path.is_file():
            raise ValueError(f"Missing Tekton Task file: {path}")
        task = yaml.safe_load(path.read_text(encoding="utf-8"))
        if task.get("kind") != "Task" or task.get("metadata", {}).get("name") != phase["task"]["name"]:
            raise ValueError(f"Task identity mismatch for phase {phase['id']}: {path}")
        declared_params = {p["name"] for p in task.get("spec", {}).get("params", [])}
        declared_workspaces = {w["name"] for w in task.get("spec", {}).get("workspaces", [])}
        for name in phase["task"].get("params", {}):
            if name not in declared_params:
                raise ValueError(f"Undeclared param {name!r} in {path}")
        for name in phase["task"].get("workspaces", {}):
            if name not in declared_workspaces:
                raise ValueError(f"Undeclared workspace {name!r} in {path}")
        for param in task.get("spec", {}).get("params", []):
            if "default" not in param and param["name"] not in phase["task"].get("params", {}):
                raise ValueError(f"Missing required Task param {param['name']!r} in {path}")
        for workspace in task.get("spec", {}).get("workspaces", []):
            if not workspace.get("optional", False) and workspace["name"] not in phase["task"].get("workspaces", {}):
                raise ValueError(f"Missing required Task workspace {workspace['name']!r} in {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    flags = parser.add_mutually_exclusive_group()
    flags.add_argument("--check", action="store_true", help="Verify generated Pipeline and referenced Tasks")
    flags.add_argument("--task-files", action="store_true", help="Print workspace-relative files for Makefile")
    args = parser.parse_args()
    if args.task_files:
        print(" ".join(os.path.relpath(p, REPO_ROOT / "pipeline/integration") for p in task_files()))
        return 0
    generated = "# GENERATED from phase_registry.json; edit the template or registry, not this file.\n" + yaml.safe_dump(
        build_pipeline(), sort_keys=False, allow_unicode=True,
    )
    if args.check:
        verify_task_interfaces()
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != generated:
            print("Generated Pipeline is out of date. Run: python -m scripts.mcp.generate_mcp_pipeline")
            return 1
        print("MCP Pipeline is in sync with phase registry; task interfaces match")
        return 0
    OUTPUT.write_text(generated, encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
