"""Exercise MCPChecker routing and the Evaluate Task's actual shell scripts.

MCPChecker/provider calls and package installation are doubles. Bash and the
report aggregator run for real; these tests do not launch Tekton or an LLM.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
TASK = REPO / "pipeline/tasks/phases/evaluate.yaml"
MCP_STEPS = ("mcpchecker-eval", "mcpchecker-aggregate")


def _steps() -> dict:
    return {step["name"]: step for step in yaml.safe_load(TASK.read_text())["spec"]["steps"]}


def _selected(step: dict, engine: str) -> bool:
    """Evaluate the Task's Tekton in/notin conditions for the requested engine."""
    for condition in step.get("when", []):
        value = condition["input"].replace("$(params.eval-engine)", engine)
        if condition["operator"] == "in":
            if value not in condition["values"]:
                return False
        elif condition["operator"] == "notin":
            if value in condition["values"]:
                return False
        else:
            raise AssertionError(f"Unhandled condition: {condition}")
    return True


@pytest.mark.parametrize("engine", ["mcpchecker", "harbor", "both", "ase", "a2a", "aeh", "aeh_openshell_openclaw"])
def test_mcpchecker_steps_only_selected_for_mcpchecker(engine: str):
    steps = _steps()
    selected = [name for name in MCP_STEPS if _selected(steps[name], engine)]
    assert selected == (list(MCP_STEPS) if engine == "mcpchecker" else [])


@pytest.fixture
def execution(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    workspace = tmp_path / "workspace"
    submission = workspace / "submissions/probe"
    submission.mkdir(parents=True)
    (submission / "eval.yaml").write_text("kind: Eval\n")
    (submission / "mcp-config.yaml").write_text('mcpServers:\n  probe:\n    url: "${MCP_URL}"\n')
    (workspace / "_pipeline").symlink_to(REPO, target_is_directory=True)
    (workspace / "_eval_tmp").mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()

    def executable(name: str, body: str) -> None:
        path = bindir / name
        path.write_text(f"#!{sys.executable}\n{body}\n")
        path.chmod(0o755)

    executable("pip", "# Dependencies are installed in the test environment; no network install.\n")
    executable("envsubst", "import os, sys\nsys.stdout.write(os.path.expandvars(sys.stdin.read()))")
    executable(
        "mcpchecker",
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['CHECKER_CALL']).write_text(json.dumps({\n"
        "    'args': sys.argv[1:],\n"
        "    'config': Path('mcp-config-resolved.yaml').read_text(),\n"
        "}))\n"
        "print('MCPChecker test output')\n"
        "print(os.environ['CHECKER_OUTPUT'])\n"
        "sys.exit(int(os.environ.get('CHECKER_EXIT', '0')))",
    )
    for name in ("python", "python3"):
        # Keep the active virtual environment when launching the real scripts.
        executable(name, "import os, sys\nos.execv(sys.executable, [sys.executable, *sys.argv[1:]])")
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "MCP_URL": "http://fixture.invalid/mcp",
        "LITELLM_PROXY_URL": "",
        "CHECKER_CALL": str(tmp_path / "checker-call.json"),
        "CHECKER_OUTPUT": "invalid output",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return workspace, env


def _run_step(name: str, workspace: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    step = _steps()[name]
    assert _selected(step, "mcpchecker"), f"{name} would be skipped by Tekton"
    script = step["script"]
    replacements = {
        "$(params.eval-engine)": "mcpchecker",
        "$(params.submission-dir)": "probe",
        "$(params.submission-name)": "probe",
        "$(params.pipeline-run-id)": "unit-test-run",
        "$(workspaces.source.path)": str(workspace),
        "/tmp/mcpchecker-": str(workspace.parent / "mcpchecker-"),
        "/tmp/mcp-result.json": str(workspace.parent / "mcp-result.json"),
    }
    for old, new in replacements.items():
        script = script.replace(old, new)
    assert not re.search(r"\$\((params|workspaces|results)\.", script), "Unresolved Tekton substitution"
    return subprocess.run(["bash", "-c", script], cwd=workspace, env=env, capture_output=True, text=True)


@pytest.mark.parametrize("passed", [True, False])
def test_selected_scripts_execute_and_produce_report(execution: tuple[Path, dict[str, str]], passed: bool):
    workspace, env = execution
    env["CHECKER_OUTPUT"] = json.dumps(
        {
            "evalName": "routing-probe",
            "results": [
                {
                    "taskName": "connection-probe",
                    "taskPassed": passed,
                    "callHistory": {"ToolCalls": [{"serverName": "probe", "name": "check_connection"}]},
                }
            ],
        }
    )
    # A task failure can have a nonzero checker exit and still provide a report.
    env["CHECKER_EXIT"] = "0" if passed else "1"
    for name in MCP_STEPS:
        result = _run_step(name, workspace, env)
        assert result.returncode == 0, result.stdout + result.stderr

    call = json.loads(Path(env["CHECKER_CALL"]).read_text())
    assert call["args"] == ["check", "eval.yaml", "--mcp-config-file", "mcp-config-resolved.yaml", "-o", "json"]
    assert yaml.safe_load(call["config"])["mcpServers"]["probe"]["url"] == env["MCP_URL"]
    reports = workspace / "reports/probe"
    report = json.loads((reports / "mcpchecker-report.json").read_text())
    assert json.loads((reports / "report.json").read_text()) == report
    assert report["total_tasks"] == 1
    assert report["passed_tasks"] == int(passed)
    assert report["tasks"][0]["tool_call_records"][0]["tool_name"] == "check_connection"
    assert report["provenance"]["pipeline_run_id"] == "unit-test-run"
    assert (workspace / "_eval_tmp/treatment-reward").read_text() == ("1.0000" if passed else "0.0000")
    assert (workspace / "_eval_tmp/recommendation").read_text() == ("pass" if passed else "fail")


def test_missing_endpoint_stops_before_checker(execution: tuple[Path, dict[str, str]]):
    workspace, env = execution
    env["MCP_URL"] = ""
    result = _run_step("mcpchecker-eval", workspace, env)
    assert result.returncode != 0
    assert "MCP_URL not set" in result.stdout
    assert not Path(env["CHECKER_CALL"]).exists()


def test_invalid_checker_output_does_not_produce_a_passing_report(execution: tuple[Path, dict[str, str]]):
    workspace, env = execution
    result = _run_step("mcpchecker-eval", workspace, env)
    assert result.returncode == 0, result.stdout + result.stderr
    result = _run_step("mcpchecker-aggregate", workspace, env)
    assert result.returncode != 0
    assert not (workspace / "reports/probe/report.json").exists()
    assert not (workspace / "_eval_tmp/recommendation").exists()


@pytest.mark.parametrize("task_path", ["pipeline/tasks/post/store.yaml", "pipeline/tasks/konflux/store.yaml"])
@pytest.mark.parametrize("passed", [True, False])
def test_store_task_persists_mcpchecker_reports(execution, task_path, passed):
    """Execute each real storage script against SQLite, including an idempotent retry."""
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from abevalflow.db.models import MCPCheckerRun, MCPCheckerTask
    from scripts.aggregate_mcpchecker import aggregate_mcpchecker_results

    workspace, env = execution
    fixture = REPO / "tests/fixtures/mcpchecker/assertion-failure.json"
    raw = json.loads(fixture.read_text())
    raw["results"][0]["allAssertionsPassed"] = passed
    raw["results"][0]["assertionResults"]["minToolCalls"]["passed"] = passed
    raw_path = workspace / "raw.json"
    raw_path.write_text(json.dumps(raw))
    report = aggregate_mcpchecker_results(raw_path, "probe", pipeline_run_id="unit-test-run")
    report_dir = workspace / "reports/probe"
    report_dir.mkdir(parents=True)
    (report_dir / "mcpchecker-report.json").write_text(report.model_dump_json())
    bindir = Path(env["PATH"].split(os.pathsep)[0])
    git = bindir / "git"
    git.write_text("#!/bin/sh\nexit 0\n")
    git.chmod(0o755)
    env["DATABASE_URL"] = f"sqlite:///{workspace / 'results.db'}"
    env["MLFLOW_TRACKING_URI"] = ""
    step = next(
        step
        for step in yaml.safe_load((REPO / task_path).read_text())["spec"]["steps"]
        if step["name"] == "store-to-db"
    )
    script = step["script"]
    for old, new in {
        "$(workspaces.source.path)": str(workspace),
        "$(params.eval-engine)": "mcpchecker",
        "$(params.pipeline-repo-revision)": "test-revision",
        "$(params.pipeline-repo-url)": "https://fixture.invalid/repo.git",
        "$(params.submission-name)": "probe",
        "$(params.pipeline-run-id)": "unit-test-run",
    }.items():
        script = script.replace(old, new)
    assert not re.search(r"\$\((params|workspaces|results)\.", script)
    for _ in range(2):
        result = subprocess.run(["bash", "-c", script], cwd=workspace, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr

    with Session(create_engine(env["DATABASE_URL"])) as session:
        runs = session.scalars(select(MCPCheckerRun)).all()
        tasks = session.scalars(select(MCPCheckerTask)).all()
        assert len(runs) == len(tasks) == 1
        assert runs[0].pipeline_run_id == "unit-test-run"
        assert runs[0].overall_score == float(passed)
        assert tasks[0].status == ("passed" if passed else "failed")
        assert tasks[0].tool_calls == 1
        assert tasks[0].details_json["assertion_results"]["minToolCalls"]["passed"] is passed
