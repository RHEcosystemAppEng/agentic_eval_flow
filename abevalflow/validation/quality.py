"""Adapt harness-eval 8's rule engine to the existing Tier 1 report contract.

Keep the former submission profile's retained checks and severities, rather
than switching to the new advice-only lint command. Rules run upstream; this
module only selects them and serializes their findings. See Docs/scanner-upgrade.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from importlib.metadata import version
from pathlib import Path

SECURITY_RULES = (
    "security/no-prompt-injection",
    "security/no-credential-access",
    "security/coercive-override",
    "security/obfuscation",
    "security/stealth-persistence",
    "security/prompt-exfiltration",
    "security/data-exfiltration",
    "security/reverse-shell",
    "security/mcp-tool-poisoning",
)
QUALITY_RULES = (
    "frontmatter/description-required",
    "frontmatter/description-quality",
    "frontmatter/format-valid",
    "content/token-budget",
    "content/broken-references",
    "content/circular-references",
    "content/description-length",
    "quality/imprecise-instruction",
    "quality/redundant-guidance",
    "quality/unfinished-content",
    "quality/stale-references",
    "quality/scope-grab-description",
)
RULES = {**dict.fromkeys(SECURITY_RULES, "error"), **dict.fromkeys(QUALITY_RULES, "warning")}
CRITICAL_RULES = {"security/stealth-persistence", "security/prompt-exfiltration"}
PROFILE = "abeval-skill-quality/v2"


def completeness_findings(skill: Path) -> list[dict]:
    """Retain submission content checks removed with the upstream scan command."""
    findings = []

    def finding(path: Path, message: str, severity: str = "low") -> None:
        findings.append(
            dict(
                severity=severity,
                rule_id="submission/file-completeness",
                message=message,
                file_path=str(path.relative_to(skill)),
                category="file_completeness",
                line=1,
            )
        )

    instruction = skill / "instruction.md"
    if instruction.is_file():
        body = re.sub(r"^---\s*\n.*?\n---\s*", "", instruction.read_text(), flags=re.DOTALL).strip()
        body = re.sub(r"^#.*\n?", "", body).strip()
        if len(body) < 50:
            finding(
                instruction,
                f"instruction.md has only {len(body)} characters of body text",
                "high" if len(body) < 10 else "low",
            )
    for path in sorted((skill / "tests").rglob("*.py")):
        if {".git", "node_modules", "vendor", "__pycache__", ".venv"}.intersection(path.relative_to(skill).parts):
            continue
        content = path.read_text()
        if "assert" not in content and "pytest.raises" not in content:
            finding(path, f"{path.relative_to(skill)} has no assert statements or pytest.raises")
    return findings


def scan(skill: Path, project_root: Path) -> tuple[dict, dict]:
    from harness_eval.inspection.engine import lint
    from harness_eval.inspection.registry import get_rule

    skill, project_root = skill.resolve(), project_root.resolve()
    if not (skill / "SKILL.md").is_file() or not skill.is_relative_to(project_root):
        raise ValueError("A readable SKILL.md within the project root is required")
    if any(get_rule(rule) is None for rule in RULES):
        raise ValueError("The installed scanner is missing required submission-profile rules")
    result = lint(str(skill), RULES, scan_state={"project_root": str(project_root)})
    if {rule.rule_id for rule in result.rules_run} != set(RULES):
        raise ValueError("The scanner did not execute every required submission-profile rule")
    security, quality = [], []
    for item in result.diagnostics:
        severity = item.severity.value
        if severity == "error":
            severity = "critical" if item.rule_id in CRITICAL_RULES else "high"
        elif severity == "warning":
            severity = "low" if item.rule_id.startswith(("quality/", "submission/")) else "medium"
        location = item.location
        path = Path(location.file) if location and location.file else skill / "SKILL.md"
        finding = dict(
            severity=severity,
            rule_id=item.rule_id,
            message=item.message,
            file_path=str(path.relative_to(skill)) if path.is_relative_to(skill) else str(path),
            category=item.rule_id.split("/", 1)[-1].replace("-", "_"),
            line=location.start_line or 0 if location else 0,
        )
        (security if item.rule_id.startswith("security/") else quality).append(finding)
    quality.extend(completeness_findings(skill))
    metadata = dict(
        scanner="harness-eval",
        version=version("harness-eval"),
        profile=PROFILE,
        rules_checked=sorted(RULES),
        suppression_count=result.suppression_count,
        completeness_checked=True,
    )
    return {"findings": quality, "metadata": metadata}, {"findings": security, "metadata": metadata}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill", type=Path)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--output-quality", type=Path, required=True)
    parser.add_argument("--output-security", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        quality, security = scan(args.skill, args.project_root)
        for path, report in ((args.output_quality, quality), (args.output_security, security)):
            path.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Quality scan failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
