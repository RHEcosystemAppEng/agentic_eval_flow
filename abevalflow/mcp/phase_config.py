"""Declarative MCP evaluation phase configuration.

A small global JSON config and the JSON files under phases/ are the single
source of truth for Compass mapping, track definitions and Tekton entries.
Adding a phase requires adding one new phases/*.json file and its Tekton Task.
MCP_PHASE_CONFIG and MCP_PHASES_DIR can be overridden for isolated tests.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_DEFAULT_PATH = Path(__file__).with_name("phase_registry.json")
_VALID_PHASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def load_phase_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path or os.environ.get("MCP_PHASE_CONFIG", _DEFAULT_PATH))
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("overall_fact_ref"):
        raise ValueError("Phase configuration must define overall_fact_ref")

    phase_dir = Path(os.environ.get("MCP_PHASES_DIR", config_path.parent / "phases"))
    phase_files = sorted(phase_dir.glob("*.json"))
    if not phase_files:
        raise ValueError(f"No phase definition files in {phase_dir}")
    phases = [json.loads(file.read_text(encoding="utf-8")) for file in phase_files]
    phases.sort(key=lambda p: (p["order"], p["id"]))

    seen: set[str] = set()
    orders: set[int] = set()
    fact_refs: set[str] = {data["overall_fact_ref"]}
    owned_check_ids: set[str] = set()
    for phase in phases:
        phase_id = phase["id"]
        if not isinstance(phase_id, str) or not _VALID_PHASE.fullmatch(phase_id):
            raise ValueError(f"Invalid phase id: {phase_id!r}")
        if phase_id in seen:
            raise ValueError(f"Duplicate phase id: {phase_id}")
        if not isinstance(phase.get("order"), int) or phase["order"] in orders:
            raise ValueError(f"Invalid/duplicate order for phase {phase_id}")
        orders.add(phase["order"])
        summary_ref = phase.get("summary_fact_ref")
        if not isinstance(summary_ref, str) or not summary_ref or summary_ref in fact_refs:
            raise ValueError(f"Missing/duplicate summary_fact_ref for {phase_id}")
        fact_refs.add(summary_ref)
        if not isinstance(phase.get("checks"), dict):
            raise ValueError(f"Phase {phase_id} must define checks as a map")
        for check_id, check in phase["checks"].items():
            if not isinstance(check, dict):
                raise ValueError(f"Invalid check definition {phase_id}/{check_id}")
            if check.get("consumed") and check.get("fact_ref"):
                raise ValueError(f"Consumed check {phase_id}/{check_id} cannot have publish fact_ref")
            if fact_ref := check.get("fact_ref"):
                if fact_ref in fact_refs:
                    raise ValueError(f"Duplicate Compass fact_ref {fact_ref!r}")
                fact_refs.add(fact_ref)
                track_check_id = "mcp-eval-" + check_id.replace("_", "-")
                if track_check_id in owned_check_ids:
                    raise ValueError(f"Duplicate Compass track check id {track_check_id!r}")
                owned_check_ids.add(track_check_id)
        task = phase.get("task")
        if task is None or not task.get("file") or not task.get("name"):
            raise ValueError(f"Incomplete Tekton task definition for {phase_id}")
        for dependency in phase.get("depends_on", []):
            if dependency not in seen:
                raise ValueError(f"Dependency {dependency!r} must appear before {phase_id}")
        seen.add(phase_id)
    data["phases"] = phases
    return data


PHASE_CONFIG = load_phase_config()
PHASES = {phase["id"]: phase for phase in PHASE_CONFIG["phases"]}
