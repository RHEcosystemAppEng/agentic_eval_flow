#!/usr/bin/env python3
"""Aggregate MCPChecker output into MCPCheckerResult report format.

MCPChecker produces a JSON output file (mcpchecker-<name>-out.json) with
detailed information about each task's execution. This script parses that
output and creates a structured MCPCheckerResult report.

Usage:
    python scripts/aggregate_mcpchecker.py \\
        --output-json /path/to/mcpchecker-out.json \\
        --submission-name my-submission \\
        --report-dir /path/to/reports/my-submission
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from abevalflow.mcpchecker_report import (
    LLMJudgeResult,
    MCPCheckerResult,
    MCPCheckerTaskResult,
    ToolCallRecord,
)
from abevalflow.report import Provenance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def parse_mcpchecker_output(output_path: Path) -> dict:
    """Load and parse MCPChecker output JSON."""
    if not output_path.exists():
        raise FileNotFoundError(f"MCPChecker output not found: {output_path}")

    with open(output_path) as f:
        return json.load(f)


def extract_task_results(raw_output: dict) -> list[MCPCheckerTaskResult]:
    """Extract task results from MCPChecker output format.

    Handles multiple MCPChecker output formats:
    - v1alpha2 format: results[] with taskName, taskPassed, callHistory, assertionResults
    - Legacy format: taskResults[] with taskId, status, toolCalls, llmJudgeResults
    """
    tasks: list[MCPCheckerTaskResult] = []

    task_results = raw_output.get("taskResults", [])
    if not task_results:
        task_results = raw_output.get("results", [])

    for task_data in task_results:
        # Handle task identification (v1alpha2 uses taskPath/taskName, legacy uses taskId)
        task_id = task_data.get("taskId", task_data.get("taskPath", task_data.get("name", "unknown")))
        task_name = task_data.get("taskName", task_data.get("name", task_id))

        # Handle status (v1alpha2 uses taskPassed boolean, legacy uses status string)
        if "taskPassed" in task_data:
            if task_data["taskPassed"]:
                status = "passed"
            elif task_data.get("agentExecutionError"):
                status = "error"
            else:
                status = "failed"
        elif "status" in task_data:
            status_raw = task_data.get("status", "unknown").lower()
            if status_raw in ("passed", "pass", "success"):
                status = "passed"
            elif status_raw in ("failed", "fail", "failure"):
                status = "failed"
            elif status_raw in ("error", "exception"):
                status = "error"
            elif status_raw in ("skipped", "skip"):
                status = "skipped"
            else:
                status = "error"
        else:
            status = "error"

        # Handle tool calls (v1alpha2 uses callHistory.ToolCalls, legacy uses toolCalls)
        call_history = task_data.get("callHistory") or {}
        if isinstance(call_history, dict):
            tool_calls_data = call_history.get("ToolCalls") or []
        else:
            tool_calls_data = call_history or []
        # Fallback to legacy toolCalls field
        if not tool_calls_data:
            tool_calls_data = task_data.get("toolCalls") or []

        tool_call_records = []
        for tc in tool_calls_data or []:
            if isinstance(tc, dict):
                # Extract arguments from request.Params.arguments if present
                request = tc.get("request") or {}
                params = request.get("Params") or {}
                arguments = params.get("arguments") or tc.get("arguments") or tc.get("params")

                tool_call_records.append(
                    ToolCallRecord(
                        server=tc.get("serverName", tc.get("server", tc.get("mcpServer", "unknown"))),
                        tool_name=tc.get("name", tc.get("tool", tc.get("toolName", "unknown"))),
                        arguments=arguments,
                        success=tc.get("success", True),
                    )
                )

        # Handle judge/assertion results (v1alpha2 uses assertionResults, legacy uses llmJudgeResults)
        llm_judge_data = (
            task_data.get("llmJudgeResults")
            or task_data.get("verifyResults")
            or task_data.get("assertionResults")
            or []
        )

        llm_judge_results = []
        for judge in llm_judge_data or []:
            if isinstance(judge, dict):
                llm_judge_results.append(
                    LLMJudgeResult(
                        check_type=judge.get("type", judge.get("checkType", judge.get("assertionType", "contains"))),
                        expected=judge.get("expected", judge.get("description", "")),
                        passed=judge.get("passed", judge.get("success", judge.get("assertionPassed", False))),
                        reason=judge.get("reason", judge.get("message", judge.get("details"))),
                    )
                )

        tasks.append(
            MCPCheckerTaskResult(
                task_id=task_id,
                task_name=task_name,
                status=status,
                tool_calls=len(tool_calls_data),
                tool_call_records=tool_call_records,
                llm_judge_results=llm_judge_results,
                duration_ms=task_data.get("durationMs", task_data.get("duration")),
                error_message=task_data.get("error", task_data.get("errorMessage")),
                agent_response=task_data.get("agentResponse", task_data.get("response", task_data.get("taskOutput"))),
            )
        )

    return tasks


def aggregate_mcpchecker_results(
    output_path: Path,
    submission_name: str,
    pipeline_run_id: str | None = None,
    commit_sha: str | None = None,
) -> MCPCheckerResult:
    """Parse MCPChecker output and create MCPCheckerResult report."""
    raw_output = parse_mcpchecker_output(output_path)

    # Extract eval name from various possible locations
    eval_name = raw_output.get("evalName", raw_output.get("name"))
    if not eval_name:
        # v1alpha2 format: summary.evals.names contains task names
        eval_names = raw_output.get("summary", {}).get("evals", {}).get("names", [])
        if eval_names:
            eval_name = f"{submission_name}-eval"
        else:
            eval_name = "unknown"

    tasks = extract_task_results(raw_output)

    passed_tasks = sum(1 for t in tasks if t.status == "passed")
    failed_tasks = sum(1 for t in tasks if t.status == "failed")
    error_tasks = sum(1 for t in tasks if t.status == "error")
    skipped_tasks = sum(1 for t in tasks if t.status == "skipped")
    total_tasks = len(tasks)

    overall_score = passed_tasks / total_tasks if total_tasks > 0 else 0.0

    total_duration_ms = raw_output.get("totalDurationMs")
    if total_duration_ms is None:
        task_durations = [t.duration_ms for t in tasks if t.duration_ms]
        if task_durations:
            total_duration_ms = sum(task_durations)

    provenance = Provenance(
        generated_at=datetime.now(UTC),
        commit_sha=commit_sha,
        pipeline_run_id=pipeline_run_id,
        eval_engine="mcpchecker",
    )

    return MCPCheckerResult(
        submission_name=submission_name,
        eval_name=eval_name,
        overall_score=overall_score,
        passed_tasks=passed_tasks,
        failed_tasks=failed_tasks,
        error_tasks=error_tasks,
        skipped_tasks=skipped_tasks,
        total_tasks=total_tasks,
        tasks=tasks,
        provenance=provenance,
        raw_output_path=str(output_path),
        total_duration_ms=total_duration_ms,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate MCPChecker output into report format")
    parser.add_argument(
        "--output-json",
        type=Path,
        required=True,
        help="Path to MCPChecker output JSON file",
    )
    parser.add_argument(
        "--submission-name",
        required=True,
        help="Name of the submission being evaluated",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        required=True,
        help="Directory to write the report to",
    )
    parser.add_argument(
        "--pipeline-run-id",
        default=None,
        help="Pipeline run ID for provenance",
    )
    parser.add_argument(
        "--commit-sha",
        default=None,
        help="Commit SHA for provenance",
    )
    args = parser.parse_args(argv)

    try:
        result = aggregate_mcpchecker_results(
            output_path=args.output_json,
            submission_name=args.submission_name,
            pipeline_run_id=args.pipeline_run_id,
            commit_sha=args.commit_sha,
        )
    except Exception as e:
        logger.exception("Failed to aggregate MCPChecker results")
        print(json.dumps({"error": str(e)}))
        return 1

    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / "mcpchecker-report.json"
    compat_report_path = args.report_dir / "report.json"

    with open(report_path, "w") as f:
        f.write(result.model_dump_json(indent=2))

    # Also write to report.json for compatibility with analyze task
    with open(compat_report_path, "w") as f:
        f.write(result.model_dump_json(indent=2))

    logger.info("Wrote report to %s (and %s)", report_path, compat_report_path)
    logger.info(
        "Results: %d/%d passed (%.1f%%), recommendation: %s",
        result.passed_tasks,
        result.total_tasks,
        result.overall_score * 100,
        result.recommendation,
    )

    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "overall_score": result.overall_score,
                "passed_tasks": result.passed_tasks,
                "total_tasks": result.total_tasks,
                "recommendation": result.recommendation,
            }
        )
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
