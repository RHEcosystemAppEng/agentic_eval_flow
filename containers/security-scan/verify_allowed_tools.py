"""Build-time regression checks against the installed scanner and its CLI."""

import json
import subprocess
import tempfile
from pathlib import Path

from skill_scanner.core.models import SkillManifest


def main() -> None:
    examples = [
        (None, []),
        ("", []),
        (" , \t ", []),
        ("Bash Read Write", ["Bash", "Read", "Write"]),
        ("Bash, Read, Write", ["Bash", "Read", "Write"]),
        (" Bash\tRead,\nWrite ", ["Bash", "Read", "Write"]),
        (["Bash", "Read"], ["Bash", "Read"]),
        ("Bash(git status:*) Read", ["Bash(git status:*)", "Read"]),
        ("Bash(echo a,b), Read", ["Bash(echo a,b)", "Read"]),
        ("Bash(echo $(pwd)) Read", ["Bash(echo $(pwd))", "Read"]),
    ]
    for tools, expected in examples:
        actual = SkillManifest(name="parser-check", description="Parser regression check", allowed_tools=tools)
        assert actual.allowed_tools == expected, (tools, actual.allowed_tools, expected)

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, tools, expect_violation in [
            ("space-tools", "Bash Read", False),
            ("comma-tools", "Bash, Read", False),
            ("missing-bash", "Read Write", True),
        ]:
            skill = root / name
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: Print a greeting when requested.\n"
                f"allowed-tools: {tools}\n---\n\n# Greeting\n\nRun `bash scripts/greet.sh`.\n"
            )
            scripts = skill / "scripts"
            scripts.mkdir()
            (scripts / "greet.sh").write_text("#!/usr/bin/env bash\nprintf 'hello\\n'\n")
            report = root / f"{name}.json"
            result = subprocess.run(
                [
                    "skill-scanner",
                    "scan",
                    str(skill),
                    "--format",
                    "json",
                    "--output-json",
                    str(report),
                    "--lenient",
                    "--fail-on-severity",
                    "high",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert report.is_file(), result.stdout + result.stderr
            findings = json.loads(report.read_text())["findings"]
            violation = any(f["rule_id"] == "ALLOWED_TOOLS_BASH_VIOLATION" for f in findings)
            assert violation == expect_violation, (name, findings)
            assert result.returncode == (1 if expect_violation else 0), (name, result.stdout, result.stderr)
    print("Allowed-tools parser and CLI checks passed; undeclared Bash still fails the HIGH gate.")


if __name__ == "__main__":
    main()
