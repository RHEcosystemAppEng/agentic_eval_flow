"""Sequential catalog orchestration around the unchanged single-skill workflow."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from abevalflow.config.runtime import ConfigurationError, RuntimeConfig, parse_destinations, parse_tiers, resolve_config
from abevalflow.results.run import write_json
from abevalflow.workflow.service import run
from abevalflow.workflow.workspace import run_lock

SKILL_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*/[a-zA-Z0-9][a-zA-Z0-9_.-]*")


class BatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_id: str
    run_dir: str
    run_id: str
    status: Literal["pending", "running", "completed", "error"] = "pending"
    exit_code: int | None = None
    reason: str = ""


class BatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = "1.0"
    batch_id: str
    source_revision: str
    tiers: tuple[int, ...]
    persist: tuple[str, ...]
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    items: list[BatchItem]

    @property
    def exit_code(self) -> int:
        if not self.completed_at or any(i.status != "completed" or i.exit_code not in (0, 1) for i in self.items):
            return 3
        return int(any(i.exit_code == 1 for i in self.items))


def validate_skills(skills: list[str]) -> None:
    if len(set(skills)) != len(skills) or any(not SKILL_ID.fullmatch(skill) for skill in skills):
        raise ConfigurationError("Skill IDs must be unique <pack>/<skill> identifiers")


def run_batch(skills: list[str], options: dict, environ=None) -> tuple[Path, BatchResult]:
    """Persist progress around each synchronous workflow; never infer retries from its exit code."""
    validate_skills(skills)
    env = os.environ if environ is None else environ
    if (
        options.get("skill") is not None
        or options.get("skill_id")
        or options.get("submission")
        or env.get("ABEVAL_SUBMISSION")
    ):
        raise ConfigurationError("batch accepts --skill-ids instead of a skill path, --skill-id, or --submission")
    batch_id = (
        options.get("run_id") or env.get("ABEVAL_RUN_ID") or f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
    )
    RuntimeConfig.valid_run_id(batch_id)
    output = Path(options.get("output") or env.get("ABEVAL_OUTPUT") or Path.cwd() / ".abeval/runs" / batch_id)
    output = output.expanduser().resolve()
    source = Path(options.get("source") or env.get("ABEVAL_SOURCE") or ".").expanduser().resolve()
    if output.is_relative_to(source) and not output.is_relative_to(source / ".abeval"):
        raise ConfigurationError("Batch output must be outside --source or under its excluded .abeval directory")
    result = BatchResult(
        batch_id=batch_id,
        source_revision=options.get("revision")
        or env.get("ABEVAL_REVISION")
        or env.get("SUBMISSION_REVISION")
        or "unknown",
        tiers=parse_tiers(options["tiers"] if options.get("tiers") is not None else env.get("ABEVAL_TIERS") or "1"),
        persist=parse_destinations(
            options["persist"] if options.get("persist") is not None else env.get("ABEVAL_PERSIST", "")
        ),
        items=[
            BatchItem(
                skill_id=skill,
                run_id=f"{batch_id[:55]}-{hashlib.sha256(skill.encode()).hexdigest()[:16]}",
                run_dir=f"runs/{index:04d}",
            )
            for index, skill in enumerate(skills)
        ],
    )

    def save():
        write_json(output / "batch.json", result.model_dump(mode="json"))

    with run_lock(output, create=True):
        save()
        try:
            for item in result.items:
                item.status = "running"
                save()
                print(f"Evaluating {item.skill_id}", flush=True)
                try:
                    config = resolve_config(
                        {
                            **options,
                            "skill_id": item.skill_id,
                            "source": source,
                            "run_id": item.run_id,
                            "output": output / item.run_dir,
                        },
                        env,
                    )
                    child = run(config)
                    item.exit_code = child.exit_code
                    item.status = "completed"
                    save()
                    if any(step.details.get("stop_batch") for step in child.steps):
                        for pending in result.items:
                            if pending.status == "pending":
                                pending.status = "error"
                                pending.reason = "Not started: previous trial cleanup could not be confirmed"
                        break
                except Exception as exc:
                    item.status = "error"
                    item.exit_code = 3
                    item.reason = f"Skill workflow failed ({type(exc).__name__})"
                save()
            result.completed_at = datetime.now(UTC)
        finally:
            save()  # Preserve running/pending states on interruption; never claim completion.
    return output, result
