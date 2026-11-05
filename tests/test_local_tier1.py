"""Installed scanners work locally without exposed scanner commands or containers."""

import json
import sysconfig
from pathlib import Path

import pytest

from abevalflow.config.runtime import resolve_config
from abevalflow.validation.scanners import local_scanner_executable
from abevalflow.workflow.readiness import check_readiness
from abevalflow.workflow.service import run


@pytest.mark.parametrize("tools,blocked", [("Bash Read", False), ("Bash, Read", False), ("Read Write", True)])
def test_installed_tier1_scanners_keep_tool_permissions_and_write_evidence(catalog, monkeypatch, tools, blocked):
    skill = catalog["skill"]
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Print a greeting when requested.\nlicense: Apache-2.0\n"
        f"allowed-tools: {tools}\n---\n\n# Greeting\n\nRun `bash scripts/greet.sh`.\n"
    )
    (skill / "scripts").mkdir()
    (skill / "scripts/greet.sh").write_text("#!/usr/bin/env bash\nprintf 'hello\\n'\n")
    original = (skill / "SKILL.md").read_bytes()
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    config = resolve_config(
        {"skill": skill, "output": catalog["output"], "run_id": catalog["run_id"], "command_timeout": 120}, {}
    )
    assert config.source == skill
    assert all(c["ready"] for c in check_readiness(config, cluster=False))
    result = run(config)
    assert result.exit_code in (0, 1), result.model_dump_json()
    assert [s.name for s in result.steps] == ["prepare", "structure", "security", "quality", "report"]
    assert all(s.status == "completed" for s in result.steps), result.model_dump_json()
    if "," not in tools:
        assert next(s for s in result.steps if s.name == "structure").verdict == "pass"
    security = next(s for s in result.steps if s.name == "security")
    violation = any(f["rule_id"] == "ALLOWED_TOOLS_BASH_VIOLATION" for f in security.details["findings"])
    assert violation == blocked
    assert (security.verdict == "fail") == blocked
    assert (skill / "SKILL.md").read_bytes() == original
    for name in ("security-scan.json", "skillmd-quality-scan.json", "skillmd-security-scan.json", "scorecard.json"):
        assert isinstance(json.loads((config.output / "reports" / name).read_text()), dict)
    assert (config.output / "reports/security-scan.sarif").is_file()


def test_bundled_scanners_resolve_from_the_cli_environment(monkeypatch):
    monkeypatch.setenv("PATH", "/does/not/exist")
    for command in ("skill-scanner", "harness-eval", "python3"):
        executable = Path(local_scanner_executable(command))
        assert executable.is_file()
        assert executable.parent == Path(sysconfig.get_path("scripts"))
    assert local_scanner_executable("/custom/security-scanner") == "/custom/security-scanner"
