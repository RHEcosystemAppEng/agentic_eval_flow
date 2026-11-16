"""Tests for MCPChecker integration (validation, aggregation, storage)."""

import json
from pathlib import Path

import pytest

from abevalflow.mcpchecker_report import (
    LLMJudgeResult,
    MCPCheckerResult,
    MCPCheckerTaskResult,
)
from abevalflow.schemas import EvalEngine
from scripts.aggregate_mcpchecker import aggregate_mcpchecker_results, extract_task_results
from scripts.validate import validate_submission


class TestMCPCheckerValidation:
    """Tests for MCPChecker submission validation."""

    @pytest.fixture
    def mcpchecker_submission_dir(self, tmp_path: Path) -> Path:
        """Create a valid MCPChecker submission directory."""
        sub_dir = tmp_path / "test-mcpchecker"
        sub_dir.mkdir()

        # metadata.yaml
        (sub_dir / "metadata.yaml").write_text("""
name: test-mcpchecker
description: Test MCPChecker submission
eval_engine: mcpchecker
mcp:
  credentials_secret: test-mcp-credentials
""")

        # eval.yaml
        (sub_dir / "eval.yaml").write_text("""
apiVersion: mcpchecker/v1
kind: Eval
metadata:
  name: test-eval
spec:
  agent:
    model: google:gemini-2.5-flash
  judge:
    model: openai:gpt-4o
""")

        # mcp-config.yaml
        (sub_dir / "mcp-config.yaml").write_text("""
mcpServers:
  - name: test-server
    url: http://localhost:3000
""")

        # tasks/
        tasks_dir = sub_dir / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "task1.yaml").write_text("""
apiVersion: mcpchecker/v1
kind: Task
metadata:
  name: health-check
spec:
  prompt: Check if the server is healthy
  assertions:
    - type: contains
      expected: healthy
""")

        return sub_dir

    def test_valid_mcpchecker_submission(self, mcpchecker_submission_dir: Path):
        """Valid MCPChecker submission should pass validation."""
        errors = validate_submission(mcpchecker_submission_dir, EvalEngine.MCPCHECKER)
        assert len(errors) == 0, f"Unexpected errors: {errors}"

    def test_missing_eval_yaml(self, mcpchecker_submission_dir: Path):
        """Missing eval.yaml should fail validation."""
        (mcpchecker_submission_dir / "eval.yaml").unlink()
        errors = validate_submission(mcpchecker_submission_dir, EvalEngine.MCPCHECKER)
        assert any("eval.yaml is required" in e for e in errors)

    def test_missing_mcp_config(self, mcpchecker_submission_dir: Path):
        """Missing mcp-config.yaml should fail validation."""
        (mcpchecker_submission_dir / "mcp-config.yaml").unlink()
        errors = validate_submission(mcpchecker_submission_dir, EvalEngine.MCPCHECKER)
        assert any("mcp-config.yaml is required" in e for e in errors)

    def test_missing_tasks_dir(self, mcpchecker_submission_dir: Path):
        """Missing tasks/ directory should fail validation."""
        import shutil

        shutil.rmtree(mcpchecker_submission_dir / "tasks")
        errors = validate_submission(mcpchecker_submission_dir, EvalEngine.MCPCHECKER)
        assert any("tasks/ directory is required" in e for e in errors)

    def test_empty_tasks_dir(self, mcpchecker_submission_dir: Path):
        """Empty tasks/ directory should fail validation."""
        for f in (mcpchecker_submission_dir / "tasks").glob("*.yaml"):
            f.unlink()
        errors = validate_submission(mcpchecker_submission_dir, EvalEngine.MCPCHECKER)
        assert any("at least one .yaml task file" in e for e in errors)

    def test_invalid_eval_yaml_kind(self, mcpchecker_submission_dir: Path):
        """Invalid 'kind' in eval.yaml should fail validation."""
        (mcpchecker_submission_dir / "eval.yaml").write_text("""
apiVersion: mcpchecker/v1
kind: WrongKind
metadata:
  name: test-eval
""")
        errors = validate_submission(mcpchecker_submission_dir, EvalEngine.MCPCHECKER)
        assert any("'kind' must be 'Eval'" in e for e in errors)

    def test_invalid_mcp_config_structure(self, mcpchecker_submission_dir: Path):
        """Missing mcpServers in mcp-config.yaml should fail validation."""
        (mcpchecker_submission_dir / "mcp-config.yaml").write_text("""
servers:
  - name: wrong-key
""")
        errors = validate_submission(mcpchecker_submission_dir, EvalEngine.MCPCHECKER)
        assert any("'mcpServers' section is required" in e for e in errors)


class TestMCPCheckerAggregation:
    """Tests for MCPChecker result aggregation."""

    @pytest.fixture
    def sample_mcpchecker_output(self) -> dict:
        """Sample MCPChecker output JSON."""
        return {
            "evalName": "test-eval",
            "taskResults": [
                {
                    "taskId": "task-1",
                    "taskName": "Health Check",
                    "status": "passed",
                    "toolCalls": [{"server": "test-server", "tool": "health", "success": True}],
                    "llmJudgeResults": [
                        {
                            "type": "contains",
                            "expected": "healthy",
                            "passed": True,
                            "reason": "Output contains 'healthy'",
                        }
                    ],
                    "durationMs": 1500,
                },
                {
                    "taskId": "task-2",
                    "taskName": "Data Query",
                    "status": "failed",
                    "toolCalls": [{"server": "test-server", "tool": "query", "success": False}],
                    "llmJudgeResults": [
                        {"type": "exact", "expected": "success", "passed": False, "reason": "Output does not match"}
                    ],
                    "durationMs": 2000,
                    "error": "Tool call failed",
                },
            ],
            "totalDurationMs": 3500,
        }

    def test_extract_task_results(self, sample_mcpchecker_output: dict):
        """Test extracting task results from MCPChecker output."""
        tasks = extract_task_results(sample_mcpchecker_output)
        assert len(tasks) == 2

        # Task 1 - passed
        assert tasks[0].task_id == "task-1"
        assert tasks[0].task_name == "Health Check"
        assert tasks[0].status == "passed"
        assert tasks[0].tool_calls == 1
        assert len(tasks[0].llm_judge_results) == 1
        assert tasks[0].llm_judge_results[0].passed is True
        assert tasks[0].duration_ms == 1500

        # Task 2 - failed
        assert tasks[1].task_id == "task-2"
        assert tasks[1].status == "failed"
        assert tasks[1].error_message == "Tool call failed"

    def test_aggregate_results(self, sample_mcpchecker_output: dict, tmp_path: Path):
        """Test full aggregation pipeline."""
        output_file = tmp_path / "mcpchecker-out.json"
        output_file.write_text(json.dumps(sample_mcpchecker_output))

        result = aggregate_mcpchecker_results(
            output_path=output_file,
            submission_name="test-submission",
            pipeline_run_id="test-run-123",
        )

        assert result.submission_name == "test-submission"
        assert result.eval_name == "test-eval"
        assert result.total_tasks == 2
        assert result.passed_tasks == 1
        assert result.failed_tasks == 1
        assert result.overall_score == 0.5
        assert result.provenance.pipeline_run_id == "test-run-123"
        assert result.provenance.eval_engine == "mcpchecker"

    def test_overall_score_calculation(self, tmp_path: Path):
        """Test that overall score is correctly calculated."""
        output = {
            "evalName": "score-test",
            "taskResults": [
                {"taskId": f"t{i}", "taskName": f"Task {i}", "status": "passed", "toolCalls": []} for i in range(7)
            ]
            + [{"taskId": f"t{i}", "taskName": f"Task {i}", "status": "failed", "toolCalls": []} for i in range(7, 10)],
        }
        output_file = tmp_path / "out.json"
        output_file.write_text(json.dumps(output))

        result = aggregate_mcpchecker_results(output_file, "test", None)
        assert result.total_tasks == 10
        assert result.passed_tasks == 7
        assert result.failed_tasks == 3
        assert result.overall_score == 0.7

    def test_recommendation_threshold(self, tmp_path: Path):
        """Test that recommendation follows 70% threshold."""
        # 70% passed - should be "pass"
        output_pass = {
            "evalName": "pass-test",
            "taskResults": [
                {"taskId": str(i), "taskName": f"Task {i}", "status": "passed" if i < 7 else "failed", "toolCalls": []}
                for i in range(10)
            ],
        }
        output_file = tmp_path / "pass.json"
        output_file.write_text(json.dumps(output_pass))
        result = aggregate_mcpchecker_results(output_file, "test", None)
        assert result.recommendation == "pass"

        # 60% passed - should be "fail"
        output_fail = {
            "evalName": "fail-test",
            "taskResults": [
                {"taskId": str(i), "taskName": f"Task {i}", "status": "passed" if i < 6 else "failed", "toolCalls": []}
                for i in range(10)
            ],
        }
        output_file = tmp_path / "fail.json"
        output_file.write_text(json.dumps(output_fail))
        result = aggregate_mcpchecker_results(output_file, "test", None)
        assert result.recommendation == "fail"

    @pytest.mark.parametrize(
        ("task_passed", "assertions_passed", "tool_assertion_passed", "expected_score"),
        [
            (True, True, True, 1.0),
            (True, False, False, 0.0),
            (True, False, True, 0.0),
            (False, True, True, 0.0),
            (True, None, False, 0.0),
            (True, True, False, 0.0),
        ],
    )
    def test_native_judge_and_assertion_verdicts(
        self, tmp_path, task_passed, assertions_passed, tool_assertion_passed, expected_score
    ):
        """Replay the live CVE smoke schema: judge success alone cannot pass a case."""
        fixture = Path(__file__).parent / "fixtures/mcpchecker/assertion-failure.json"
        output = json.loads(fixture.read_text())
        task = output["results"][0]
        task["taskPassed"] = task_passed
        if assertions_passed is None:
            task.pop("allAssertionsPassed")
        else:
            task["allAssertionsPassed"] = assertions_passed
        task["assertionResults"]["minToolCalls"]["passed"] = tool_assertion_passed
        task["verifyOutput"]["Steps"][0]["success"] = task_passed
        output_path = tmp_path / "native-output.json"
        output_path.write_text(json.dumps(output))

        report = aggregate_mcpchecker_results(output_path, "native-smoke")

        assert report.total_tasks == 1
        assert report.overall_score == expected_score
        assert report.recommendation == ("pass" if expected_score else "fail")
        parsed = report.tasks[0]
        assert parsed.tool_calls == 1
        assert parsed.tool_call_records[0].tool_name == "get_cve_by_id"
        assert parsed.tool_call_records[0].arguments == {"cve_id": "CVE-2016-10739"}
        assert parsed.assertion_results["minToolCalls"]["passed"] is tool_assertion_passed
        assert parsed.llm_judge_results[0].check_type == "llmJudge"
        assert parsed.llm_judge_results[0].passed is task_passed
        if not tool_assertion_passed:
            assert parsed.assertion_results["minToolCalls"]["reason"] == "Too few tool calls: expected >= 10000, got 1"


class TestMCPCheckerResultModel:
    """Tests for MCPCheckerResult Pydantic model."""

    def test_model_creation(self):
        """Test creating MCPCheckerResult model."""
        result = MCPCheckerResult(
            submission_name="test-sub",
            eval_name="test-eval",
            overall_score=0.8,
            passed_tasks=4,
            failed_tasks=1,
            total_tasks=5,
            tasks=[
                MCPCheckerTaskResult(
                    task_id="t1",
                    task_name="Task 1",
                    status="passed",
                    tool_calls=2,
                    llm_judge_results=[
                        LLMJudgeResult(
                            check_type="contains",
                            expected="test",
                            passed=True,
                            reason="Found",
                        )
                    ],
                )
            ],
        )

        assert result.submission_name == "test-sub"
        assert result.overall_score == 0.8
        assert result.recommendation == "pass"
        assert result.provenance.eval_engine == "mcpchecker"

    def test_task_llm_judge_pass_rate(self):
        """Test LLM judge pass rate calculation."""
        task = MCPCheckerTaskResult(
            task_id="t1",
            task_name="Task 1",
            status="passed",
            tool_calls=0,
            llm_judge_results=[
                LLMJudgeResult(check_type="contains", expected="a", passed=True),
                LLMJudgeResult(check_type="contains", expected="b", passed=True),
                LLMJudgeResult(check_type="exact", expected="c", passed=False),
            ],
        )

        assert task.llm_judge_pass_rate == pytest.approx(2 / 3)

    def test_task_llm_judge_pass_rate_empty(self):
        """Test LLM judge pass rate when no checks."""
        task = MCPCheckerTaskResult(
            task_id="t1",
            task_name="Task 1",
            status="passed",
            tool_calls=0,
        )

        assert task.llm_judge_pass_rate is None

    def test_json_serialization(self):
        """Test JSON serialization round-trip."""
        result = MCPCheckerResult(
            submission_name="test",
            eval_name="eval",
            overall_score=1.0,
            passed_tasks=5,
            failed_tasks=0,
            total_tasks=5,
            tasks=[],
        )

        json_str = result.model_dump_json()
        parsed = MCPCheckerResult.model_validate_json(json_str)

        assert parsed.submission_name == result.submission_name
        assert parsed.overall_score == result.overall_score
