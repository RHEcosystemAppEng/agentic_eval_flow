"""Versioned execution evidence shared by local and container workflows."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field


class StepResult(BaseModel):
    """Execution outcome, policy verdict, and artifacts for one operation."""

    name: str
    tier: int | None = None
    status: Literal["completed", "skipped", "error"] = "completed"
    verdict: Literal["pass", "warn", "fail", "unavailable"] = "pass"
    blocking: bool = True
    reason: str = ""
    artifacts: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    """Durable evidence for a workflow, including partial and failed runs."""

    schema_version: str = "1.0"
    run_id: str
    skill: str
    source_revision: str
    evaluator_version: str
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    requested_steps: list[str] = Field(default_factory=list)
    steps: list[StepResult] = Field(default_factory=list)
    publication: list[StepResult] = Field(default_factory=list)

    @computed_field
    @property
    def evidence_complete(self) -> bool:
        completed = {step.name for step in self.steps if step.status == "completed"}
        return all(name in completed for name in self.requested_steps if name != "report")

    @property
    def exit_code(self) -> int:
        checks = [s for s in self.steps + self.publication if s.blocking]
        if any(s.status == "error" for s in checks):
            return 3
        if any(s.verdict == "fail" for s in checks):
            return 1
        return 0

    def record(self, step: StepResult) -> None:
        self.steps = [s for s in self.steps if s.name != step.name] + [step]


def write_json(path: Path, value: Any) -> None:
    """Atomically replace a JSON artifact in its destination directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
