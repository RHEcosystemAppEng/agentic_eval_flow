"""Native task preparation and opt-in tests against a complete upstream runtime."""

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

from abevalflow.execution.harbor import HarborRunner
from abevalflow.execution.mcp import swap_mock_mcp_servers
from abevalflow.execution.runtime import resolve_runtime
from abevalflow.harbor_extensions.aeh_task_enrichment import enrich_harbor_tasks


@pytest.fixture
def mock_submission(tmp_path):
    pack = tmp_path / "pack"
    skill = pack / "skills/demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: demo\ndescription: Use when checking a fixture.\n---\n# Demo\n")
    (pack / "mcps.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fixture": {"url": "https://unused.invalid/mcp"},
                    "unmocked": {"command": "must-not-run", "env": {"TOKEN": "${REAL_TOKEN}"}},
                }
            }
        )
    )
    submission = tmp_path / "submissions/pack/demo"
    schema = submission.parent / "mcp-servers/fixture/schema.json"
    schema.parent.mkdir(parents=True)
    schema.write_text(
        json.dumps(
            {
                "name": "fixture",
                "version": "1.0.0",
                "tools": [
                    {
                        "name": "lookup",
                        "description": "Read the case value",
                        "inputSchema": {"type": "object", "properties": {}},
                        "outputSchema": {"type": "object", "properties": {"value": {"type": "string"}}},
                        "outputExample": {"value": "fallback"},
                    }
                ],
            }
        )
    )
    for case, value in (("case-001", "first"), ("case-002", "second")):
        directory = submission / "cases" / case
        fixture = directory / "mock-mcps/fixture/fixture.json"
        fixture.parent.mkdir(parents=True)
        fixture.write_text(json.dumps({"sequence": [{"tool": "lookup", "input": {}, "output": {"value": value}}]}))
        (directory / "input.yaml").write_text("prompt: Read the fixture\n")
        (directory / "annotations.yaml").write_text(f"expected: {value}\n")
    config = submission / "eval.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "skill": "demo",
                "runner": {"type": "claude-code", "plugin_dirs": ["../../../pack"]},
                "models": {"skill": "test-agent"},
                "dataset": {"path": "cases"},
                "execution": {"arguments": "{prompt}"},
                "judges": [
                    {
                        "name": "ground_truth",
                        "type": "check",
                        "check": (
                            "return outputs['annotations'].get('expected') in ('first', 'second'), "
                            "'ground truth loaded'"
                        ),
                    }
                ],
            }
        )
    )
    authored = yaml.safe_load(config.read_text())
    (submission / "eval-treatment.yaml").write_text(yaml.safe_dump(authored))
    authored["runner"]["plugin_dirs"] = ["../../../pack-mcp-only"]
    (submission / "eval-control.yaml").write_text(yaml.safe_dump(authored))
    return config


def test_native_mcp_inputs_preserve_fixtures_without_rewriting_verifier(mock_submission, tmp_path):
    tasks = tmp_path / "tasks"
    script = "#!/bin/bash\npython3 -m agent_eval.harbor.reward --config /tests/eval.yaml --case-dir /workspace\n"
    swap_mock_mcp_servers(mock_submission.parent)
    for case in ("case-001", "case-002"):
        task = tasks / case
        (task / "tests").mkdir(parents=True)
        (task / "task.toml").write_text('[environment]\ndocker_image="trial:test"\nworkdir="/workspace"\n')
        (task / "tests/eval.yaml").write_text('dataset:\n  path: ""\n')
        (task / "tests/test.sh").write_text(script)
    assert enrich_harbor_tasks(tasks, config_path=mock_submission) == 2
    for case, expected in (("case-001", "first"), ("case-002", "second")):
        task = tasks / case
        native = tomllib.loads((task / "task.toml").read_text())["environment"]["mcp_servers"]
        assert [server["name"] for server in native] == ["fixture"]
        assert native[0]["transport"] == "stdio"
        assert native[0]["command"] == "python3"
        assert "/workspace/mock-mcps/fixture/fixture.json" in native[0]["args"]
        assert (task / "tests/test.sh").read_text() == script
        fixture = json.loads((task / "environment/mock-mcps/fixture/fixture.json").read_text())
        assert fixture["sequence"][0]["output"]["value"] == expected
        assert (task / "tests/cases/workspace/annotations.yaml").is_file()
        assert not (task / "environment/annotations.yaml").exists()


def test_submission_without_fixtures_registers_no_mcp_servers(mock_submission, tmp_path):
    import shutil

    for case in (mock_submission.parent / "cases").iterdir():
        shutil.rmtree(case / "mock-mcps")
    task = tmp_path / "tasks/case-001"
    (task / "tests").mkdir(parents=True)
    (task / "task.toml").write_text(
        '[environment]\ndocker_image="trial:test"\n'
        '[[environment.mcp_servers]]\nname="unmocked"\ntransport="stdio"\ncommand="must-not-run"\n'
    )
    (task / "tests/eval.yaml").write_text('dataset:\n  path: ""\n')
    assert enrich_harbor_tasks(task.parent, config_path=mock_submission) == 1
    assert tomllib.loads((task / "task.toml").read_text())["environment"]["mcp_servers"] == []


@pytest.mark.skipif(not os.environ.get("ABEVAL_TEST_AEH_ROOT"), reason="Complete external AEH runtime not selected")
def test_unmodified_upstream_generates_and_validates_native_mcp_tasks(mock_submission, tmp_path):
    runtime = resolve_runtime(Path(os.environ["ABEVAL_TEST_AEH_ROOT"]))
    tasks = tmp_path / "native-tasks"
    runner = HarborRunner(model="test-agent", image="trial:test", aeh_root=runtime.root, aeh_python=runtime.python)
    runner._prepare_enriched_tasks(mock_submission, tasks)
    for task in sorted(tasks.iterdir()):
        check = (
            "import sys, tomllib; from pathlib import Path; from harbor.models.task.config import TaskConfig; "
            "config=TaskConfig.model_validate(tomllib.loads(Path(sys.argv[1]).read_text())); "
            "assert [s.name for s in config.environment.mcp_servers] == ['fixture']; "
            "assert config.environment.mcp_servers[0].transport == 'stdio'"
        )
        assert runtime.run(["-c", check, str(task / "task.toml")], environment=None, timeout=30) == 0
    assert subprocess.run(["git", "-C", str(runtime.root), "diff", "--exit-code"], check=False).returncode == 0


@pytest.mark.skipif(
    not (os.environ.get("ABEVAL_TEST_AEH_ROOT") and os.environ.get("ABEVAL_TEST_TRIAL_IMAGE")),
    reason="Complete AEH runtime and local trial image not selected",
)
@pytest.mark.parametrize("arm", ["control", "treatment"])
def test_trial_image_stdio_mock_and_native_verifier(mock_submission, tmp_path, arm):
    runtime = resolve_runtime(Path(os.environ["ABEVAL_TEST_AEH_ROOT"]))
    tasks = tmp_path / arm
    config = mock_submission.with_name(f"eval-{arm}.yaml")
    runner = HarborRunner(model="test-agent", image="trial:test", aeh_root=runtime.root, aeh_python=runtime.python)
    runner._prepare_enriched_tasks(config, tasks)
    code = """
import asyncio, json, os, shutil, subprocess
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
for entry in Path("/inputs").iterdir():
    target = Path("/workspace") / entry.name
    if entry.is_dir():
        shutil.copytree(entry, target)
    else:
        shutil.copy2(entry, target)

async def check():
    config_dir = Path("/tmp/native-claude-config")
    config_dir.mkdir()
    os.environ["CLAUDE_CONFIG_DIR"] = str(config_dir)
    subprocess.run(["bash", "/tests/register-mcp.sh"], check=True)
    server = json.loads((config_dir / ".claude.json").read_text())["mcpServers"]["fixture"]
    params = StdioServerParameters(command=server["command"], args=server["args"])
    for _ in range(2):
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert any(tool.name == "lookup" for tool in tools.tools)
                result = await session.call_tool("lookup", {})
                assert not result.is_error, result
                assert os.environ["EXPECTED"] in json.dumps(result.model_dump()), result
asyncio.run(check())
    with Path("/logs/verifier/test-stdout.txt").open("w") as output:
        subprocess.run(["/tests/test.sh"], stdout=output, stderr=subprocess.STDOUT, check=True)
reward = json.loads(Path("/logs/verifier/reward.json").read_text())
assert reward.get("reward") == 1.0 or reward.get("ground_truth") == 1.0, reward
print("Native stdio fixture and AEH verifier passed")
"""
    for case, expected in (("case-001", "first"), ("case-002", "second")):
        task = tasks / case
        assert (task / "environment/skills").exists() == (arm == "treatment")
        register = (
            "import sys, tomllib; from pathlib import Path; "
            "from harbor.models.task.config import TaskConfig; "
            "from harbor.agents.installed.claude_code import ClaudeCode; "
            "config=TaskConfig.model_validate(tomllib.loads(Path(sys.argv[1]).read_text())); "
            "agent=ClaudeCode(logs_dir=Path(sys.argv[2]), model_name='test-agent', "
            "mcp_servers=config.environment.mcp_servers); "
            "Path(sys.argv[3]).write_text(agent._build_register_mcp_servers_command())"
        )
        assert (
            runtime.run(
                [
                    "-c",
                    register,
                    str(task / "task.toml"),
                    str(task / "agent-logs"),
                    str(task / "tests/register-mcp.sh"),
                ],
                environment=None,
                timeout=30,
            )
            == 0
        )
        subprocess.run(
            [
                "podman",
                "run",
                "--rm",
                "--network=none",
                "-i",
                "-v",
                f"{task / 'environment'}:/inputs:ro",
                "-v",
                f"{task / 'tests'}:/tests:ro",
                "-e",
                f"EXPECTED={expected}",
                "--entrypoint",
                "python3",
                os.environ["ABEVAL_TEST_TRIAL_IMAGE"],
                "-",
            ],
            input=code,
            text=True,
            check=True,
            timeout=120,
        )
