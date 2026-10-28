"""Declarative MCP evaluation phase configuration.

The bundled JSON file is the single source of truth for phase metadata, Compass
fact mappings, and Tekton bundle/pipeline entries. The optional environment
variable MCP_PHASE_CONFIG can point at another JSON file (useful for local
validation before adopting a new phase).
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
    if not isinstance(data, dict) or not isinstance(data.get("phases"), list):
        raise ValueError(f"Invalid phase configuration in {config_path}: missing phases list")
    if not data.get("overall_fact_ref"):
        raise ValueError("Phase configuration must define overall_fact_ref")

    seen: set[str] = set()
    ordered_ids: list[str] = []
    for phase in data["phases"]:
        phase_id = phase["id"]
        if not isinstance(phase_id, str) or not _VALID_PHASE.fullmatch(phase_id):
            raise ValueError(f"Invalid phase id: {phase_id!r}")
        if phase_id in seen:
            raise ValueError(f"Duplicate phase id: {phase_id}")
        seen.add(phase_id)
        ordered_ids.append(phase_id)
        if not isinstance(phase.get("checks"), dict):
            raise ValueError(f"Phase {phase_id} must define checks as a map")
        for check_id, check in phase["checks"].items():
            if not isinstance(check, dict):
                raise ValueError(f"Invalid check definition {phase_id}/{check_id}")
            if check.get("consumed") and check.get("fact_ref"):
                raise ValueError(f"Consumed check {phase_id}/{check_id} cannot have a publish fact_ref")
        task = phase.get("task")
        if task is not None and (not task.get("file") or not task.get("name")):
            raise ValueError(f"Incomplete task definition for {phase_id}")
    for index, phase in enumerate(data["phases"]):
        for dependency in phase.get("depends_on", []):
            if dependency not in ordered_ids[:index]:
                raise ValueError(f"Dependency {dependency!r} must appear before {phase['id']}")
    return data


PHASE_CONFIG = load_phase_config()
PHASES = {phase["id"]: phase for phase in PHASE_CONFIG["phases"]}
