"""One workflow implementation for local execution and isolated container phases."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from abevalflow.config.runtime import RuntimeConfig
from abevalflow.results.run import RunResult, StepResult, write_json
from abevalflow.workflow.operations import Operations
from abevalflow.workflow.planning import build_plan, public_config
from abevalflow.workflow.workspace import run_lock


def save_run(config: RuntimeConfig, result: RunResult) -> None:
    write_json(config.output / "run.json", result.model_dump(mode="json"))


def create_result(config: RuntimeConfig) -> RunResult:
    try:
        evaluator_version = version("abevalflow")
    except PackageNotFoundError:
        evaluator_version = "0.5.1"
    return RunResult(
        run_id=config.run_id,
        skill=config.name,
        plugin_name=config.plugin,
        source_revision=config.revision,
        evaluator_version=evaluator_version,
        config=public_config(config),
        requested_steps=build_plan(config)["steps"],
    )


def execute_step(config: RuntimeConfig, result: RunResult, name: str, operations: Operations) -> StepResult:
    """Apply dependencies and preserve errors as evidence without losing finalization."""
    tier = 1 if name in {"structure", "security", "quality", "skill-review"} else 2
    if name in {"prepare", "report"}:
        tier = None
    prior = {s.name: s for s in result.steps}
    reason = ""
    if name != "prepare" and name != "report":
        if "prepare" not in prior or prior["prepare"].status != "completed":
            reason = "Source preparation did not complete"
        elif (
            tier == 2
            and 1 in config.tiers
            and any(required not in prior for required in ("structure", "security", "quality"))
        ):
            reason = "Tier 1 phases have not completed"
        elif name == "evaluate" and config.submission_review and "submission-review" not in prior:
            reason = "Selected submission review has not run"
        elif (
            tier == 2
            and not config.continue_on_tier1_failure
            and any(s.tier == 1 and s.blocking and (s.status == "error" or s.verdict == "fail") for s in result.steps)
        ):
            reason = "Blocking Tier 1 checks did not pass"
        elif name in {"submission-review", "evaluate"} and (
            "submission" not in prior or prior["submission"].verdict != "pass"
        ):
            reason = "Submission validation did not pass"
        elif name == "evaluate" and any(
            s.name == "submission-review" and s.blocking and (s.status == "error" or s.verdict == "fail")
            for s in result.steps
        ):
            reason = "Required submission review did not pass"
    if reason:
        step = StepResult(name=name, tier=tier, status="skipped", verdict="unavailable", reason=reason)
    else:
        try:
            step = operations.execute(name, result)
        except Exception as exc:
            from abevalflow.execution.cleanup import CleanupError

            blocking = name != "skill-review" and not (
                name == "submission-review"
                and not (
                    config.metadata
                    and config.metadata.gate_policy
                    and config.metadata.gate_policy.get_gate_policy("quality").mode.value == "block"
                )
            )
            step = StepResult(
                name=name,
                tier=tier,
                status="error",
                verdict="unavailable",
                blocking=blocking,
                reason=f"{name} failed ({type(exc).__name__})",
                details={"error_type": type(exc).__name__, "stop_batch": isinstance(exc, CleanupError)},
            )
    result.record(step)
    save_run(config, result)
    return step


def run(config: RuntimeConfig, *, operations: Operations | None = None) -> RunResult:
    """Execute all selected tiers, always report available evidence, then attempt publication."""
    with run_lock(config.output, create=True):
        config.output.chmod(0o700)
        result = create_result(config)
        save_run(config, result)
        operations = operations or Operations(config)
        for name in build_plan(config)["steps"]:
            execute_step(config, result, name, operations)
        result.completed_at = datetime.now(UTC)
        save_run(config, result)
        if config.persist:
            from abevalflow.persistence.service import publish

            result.publication = publish(config, result)
            save_run(config, result)
        return result


def load_run(directory: Path) -> tuple[RuntimeConfig, RunResult]:
    """Restore saved non-secret inputs and resolve current credentials for an existing run."""
    from abevalflow.config.runtime import refresh_credentials

    result = RunResult.model_validate_json((directory / "run.json").read_text())
    config = RuntimeConfig.model_validate(result.config)
    if directory.resolve() != config.output.resolve():
        raise ValueError("Run directory does not match its manifest")
    return refresh_credentials(config), result


def phase(directory: Path, name: str, *, if_selected: bool = False) -> RunResult:
    """Execute a declared phase once; existing evidence is not silently overwritten."""
    with run_lock(directory):
        config, result = load_run(directory)
        if name not in build_plan(config)["steps"]:
            if if_selected:
                return result
            raise ValueError(f"Phase {name} is not selected for this run")
        if any(s.name == name for s in result.steps):
            raise ValueError(f"Phase {name} already has evidence; start a new run to execute it again")
        execute_step(config, result, name, Operations(config))
        if name == "report" and result.evidence_complete:
            result.completed_at = datetime.now(UTC)
            save_run(config, result)
        return result
