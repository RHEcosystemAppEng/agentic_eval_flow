"""Regression checks for declarative MCP phases and the generated Pipeline."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
import pytest

from abevalflow.mcp.phase_config import PHASES, load_phase_config
from scripts.mcp.generate_mcp_pipeline import build_pipeline

ROOT = Path(__file__).resolve().parents[1]


def test_registry_and_pipeline_task_order():
    pipeline = build_pipeline()
    assert [task["name"] for task in pipeline["spec"]["tasks"]] == list(PHASES)
    assert pipeline["spec"]["finally"][0]["name"] == "compass-publish"
    assert pipeline == yaml.safe_load((ROOT / "pipeline/pipelines/mcp-eval-pipeline.yaml").read_text())


def test_track_levels_contain_checks_from_correct_phase():
    # Not available when reviewing only the uploaded subset of the repository.
    pytest.importorskip("abevalflow.compass_facts")
    from scripts.mcp.bootstrap_compass_track import TRACK_DEFINITION

    levels = TRACK_DEFINITION["levels"]
    assert [len(level["checks"]) for level in levels] == [3, 10, 3]
    assert levels[0]["checks"][0]["id"] == "mcp-eval-mcp-secrets"
    assert levels[2]["checks"][0]["id"] == "mcp-eval-tool-correctness"


def test_new_phase_is_discovered_without_python_changes(tmp_path):
    phase_dir = tmp_path / "phases"
    shutil.copytree(ROOT / "abevalflow/mcp/phases", phase_dir)
    config_path = tmp_path / "phase_registry.json"
    shutil.copy(ROOT / "abevalflow/mcp/phase_registry.json", config_path)
    new_phase = {
        "order": 40,
        "id": "performance-eval",
        "display_name": "Performance",
        "description": "Synthetic test phase",
        "source": "pipeline",
        "summary_fact_ref": "abevalflow:default/mcp_phase4",
        "checks": {
            "latency-check": {
                "name": "Latency",
                "track_name": "Latency",
                "description": "Response latency",
                "fact_ref": "abevalflow:default/mcp_latency",
            }
        },
        "task": {
            "name": "mcp-performance-eval",
            "file": "pipeline/tasks/konflux/mcp-performance-eval.yaml",
            "params": {"mcp-eval-image": "$(params.mcp-eval-image)"},
            "workspaces": {"source": "workspace"},
        },
        "depends_on": ["behavioral-eval"],
    }
    (phase_dir / "04-performance-eval.json").write_text(json.dumps(new_phase))
    config = load_phase_config(config_path)
    assert config["phases"][-1]["id"] == "performance-eval"

    env = {
        **os.environ,
        "MCP_PHASE_CONFIG": str(config_path),
        "MCP_PHASES_DIR": str(phase_dir),
    }
    script = """
from abevalflow.mcp.phase_config import PHASES
from scripts.mcp.generate_mcp_pipeline import build_pipeline
assert 'performance-eval' in PHASES
assert build_pipeline()['spec']['tasks'][-1]['name'] == 'performance-eval'
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=env, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
