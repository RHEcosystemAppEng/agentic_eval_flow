"""Pydantic models for MCPChecker evaluation reports.

MCPChecker evaluates MCP servers/agents by running tasks and verifying
tool usage and output correctness. Unlike Harbor/ASE which use A/B
comparison (treatment vs control), MCPChecker is single-agent evaluation.

These models define the structure of the JSON report produced by
the mcpchecker-eval task.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, computed_field

from abevalflow.report import Provenance


class LLMJudgeResult(BaseModel):
    """Result from a single LLM judge verification check."""

    check_type: Literal["contains", "exact", "llmJudge"] = Field(
        description="Type of LLM judge check, including native llmJudge phase results"
    )
    expected: str = Field(description="Expected content or reference answer")
    passed: bool = Field(description="Whether the check passed")
    reason: str | None = Field(
        default=None,
        description="Explanation from the judge about the verdict",
    )


class ToolCallRecord(BaseModel):
    """Record of a tool call made by the agent."""

    server: str = Field(description="MCP server identifier")
    tool_name: str = Field(description="Name of the tool called")
    arguments: dict | None = Field(default=None, description="Tool call arguments")
    success: bool = Field(default=True, description="Whether the call succeeded")


class MCPCheckerTaskResult(BaseModel):
    """Result from a single MCPChecker task."""

    task_id: str = Field(description="Unique task identifier from metadata.name")
    task_name: str = Field(description="Human-readable task name")
    status: Literal["passed", "failed", "error", "skipped"] = Field(description="Task execution status")
    tool_calls: int = Field(default=0, description="Number of tool calls made")
    tool_call_records: list[ToolCallRecord] = Field(
        default_factory=list,
        description="Detailed record of each tool call",
    )
    llm_judge_results: list[LLMJudgeResult] = Field(
        default_factory=list,
        description="Results from LLM judge verification checks",
    )
    assertion_results: dict[str, dict] = Field(
        default_factory=dict,
        description="Named MCPChecker assertions, including their verdicts and failure reasons",
    )
    duration_ms: int | None = Field(
        default=None,
        description="Task execution duration in milliseconds",
    )
    error_message: str | None = Field(
        default=None,
        description="Error message if status is 'error'",
    )
    agent_response: str | None = Field(
        default=None,
        description="Final response from the agent",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def llm_judge_pass_rate(self) -> float | None:
        """Pass rate for LLM judge checks. None if no checks."""
        if not self.llm_judge_results:
            return None
        passed = sum(1 for r in self.llm_judge_results if r.passed)
        return passed / len(self.llm_judge_results)


class MCPCheckerResult(BaseModel):
    """Top-level MCPChecker evaluation report.

    This is the main output from the mcpchecker-eval task, written to
    reports/{submission}/mcpchecker-report.json.
    """

    submission_name: str = Field(description="Name of the submission being evaluated")
    eval_name: str = Field(description="Name from eval.yaml metadata.name")
    overall_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Overall score: passed_tasks / total_tasks",
    )
    passed_tasks: int = Field(ge=0, description="Number of tasks that passed")
    failed_tasks: int = Field(ge=0, description="Number of tasks that failed")
    error_tasks: int = Field(default=0, ge=0, description="Number of tasks with errors")
    skipped_tasks: int = Field(default=0, ge=0, description="Number of skipped tasks")
    total_tasks: int = Field(ge=0, description="Total number of tasks")
    tasks: list[MCPCheckerTaskResult] = Field(
        description="Per-task results",
    )
    provenance: Provenance = Field(
        default_factory=Provenance,
        description="Run provenance metadata",
    )
    raw_output_path: str | None = Field(
        default=None,
        description="Path to the raw mcpchecker output JSON file",
    )
    total_duration_ms: int | None = Field(
        default=None,
        description="Total evaluation duration in milliseconds",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def recommendation(self) -> str:
        """Recommendation based on overall score.

        Uses 70% threshold for pass (configurable in future).
        """
        return "pass" if self.overall_score >= 0.7 else "fail"

    def model_post_init(self, __context) -> None:
        """Set eval_engine in provenance."""
        self.provenance.eval_engine = "mcpchecker"
