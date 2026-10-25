"""Validate skill metadata, directory layout, and content conventions.

Checks include agentskills.io metadata requirements and Apache-2.0 licensing.
Warnings remain advisory. Unreadable files and YAML frontmatter that is not a
mapping produce validation errors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from abevalflow.certification import CheckId, CheckResult

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
MAX_NAME_LEN = 64
MAX_DESCRIPTION_LEN = 1024
MAX_COMPATIBILITY_LEN = 500
MAX_LINE_COUNT = 500
ALLOWED_SUBDIRS = {"scripts", "references", "assets"}
DEPRECATED_SUBDIRS = {"resources"}

ASCII_ART_PATTERN = re.compile(r"[─│┌┐└┘├┤┬┴┼╭╮╯╰═║╔╗╚╝╠╣╦╩╬↑↓←→↔⇒⇐⇔▲▼◄►]{3,}")
PERSONA_PATTERN = re.compile(r"^[  ]*You are (a|an|the) ", re.IGNORECASE | re.MULTILINE)
ROUTING_KEYWORDS = re.compile(r"(use when|don't use|not for|triggers on)", re.IGNORECASE)
MARKETING_BUZZWORDS = re.compile(
    r"(comprehensive|powerful|robust|cutting-edge|world-class"
    r"|state-of-the-art|best-in-class|game-changing)",
    re.IGNORECASE,
)


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _strip_fenced_blocks(content: str) -> str:
    """Remove fenced code blocks, returning only prose content."""
    lines = content.splitlines()
    result: list[str] = []
    in_fence = False
    for line in lines:
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            result.append(line)
    return "\n".join(result)


def _extract_frontmatter(content: str) -> tuple[str | None, str | None]:
    """Return (frontmatter_text, error_message).

    If frontmatter is valid, error_message is None. Otherwise frontmatter_text
    is None and error_message explains the problem.
    """
    lines = content.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return None, "Missing frontmatter opening delimiter (---)"
    closing = None
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":
            closing = i
            break
    if closing is None:
        return None, "Missing frontmatter closing delimiter (---)"
    return "\n".join(lines[1:closing]), None


def _parse_frontmatter(fm_text: str) -> dict | None:
    """Parse YAML frontmatter text, return dict or None on error."""
    try:
        parsed = yaml.safe_load(fm_text)
        if parsed is None:
            return {}
        return parsed if isinstance(parsed, dict) else None
    except yaml.YAMLError:
        return None


# ── Per-skill validation ─────────────────────────────────────────────────────


def validate_skill(skill_path: Path) -> ValidationResult:
    """Run all Tier 1 (agentskills.io) checks on a single skill."""
    result = ValidationResult()
    skill_dir = skill_path.parent
    dir_name = skill_dir.name
    try:
        content = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        result.errors.append(f"Cannot read SKILL.md: {exc}")
        return result

    # 1. Frontmatter delimiters
    fm_text, fm_err = _extract_frontmatter(content)
    if fm_err:
        result.errors.append(fm_err)
        return result  # can't continue without frontmatter

    fm = _parse_frontmatter(fm_text)
    if fm is None:
        result.errors.append("Invalid YAML in frontmatter (expected a mapping)")
        return result

    # 2. name field
    name = fm.get("name")
    if not name or not isinstance(name, str):
        result.errors.append("Missing required field: name")
        return result

    name = name.strip()
    name_len = len(name)
    if name_len < 1 or name_len > MAX_NAME_LEN:
        result.errors.append(f"Name length must be 1-{MAX_NAME_LEN} chars (got {name_len})")
        return result

    if not NAME_PATTERN.match(name):
        if name.startswith("-"):
            result.errors.append(f"Name cannot start with hyphen: {name}")
        elif name.endswith("-"):
            result.errors.append(f"Name cannot end with hyphen: {name}")
        elif "--" in name:
            result.errors.append(f"Name cannot contain consecutive hyphens: {name}")
        elif any(c.isupper() for c in name):
            result.errors.append(f"Name must be lowercase: {name}")
        elif "_" in name:
            result.errors.append(f"Name cannot contain underscores (use hyphens): {name}")
        else:
            result.errors.append(f"Name must match pattern ^[a-z][a-z0-9]*(-[a-z0-9]+)*$: {name}")
        return result

    # name must match directory
    if name != dir_name:
        result.errors.append(f"Name '{name}' does not match directory name '{dir_name}'")
        return result

    # 3. description field
    desc_raw = fm.get("description")
    if not desc_raw:
        result.errors.append("Missing required field: description")
        return result

    desc = str(desc_raw).strip()
    desc_len = len(desc)
    if desc_len < 1:
        result.errors.append("Description cannot be empty")
        return result
    if desc_len > MAX_DESCRIPTION_LEN:
        result.errors.append(f"Description exceeds {MAX_DESCRIPTION_LEN} chars (got {desc_len})")
        return result

    # 4. license field
    license_val = fm.get("license")
    if not license_val:
        result.errors.append("Missing required field: license (must be 'Apache-2.0')")
        return result
    if str(license_val).strip() != "Apache-2.0":
        result.errors.append(f"License must be 'Apache-2.0' (got '{license_val}')")
        return result

    # 5. compatibility (optional, ≤500 chars)
    compat = fm.get("compatibility")
    if compat:
        compat_len = len(str(compat).strip())
        if compat_len > MAX_COMPATIBILITY_LEN:
            result.errors.append(f"Compatibility exceeds {MAX_COMPATIBILITY_LEN} chars (got {compat_len})")
            return result

    # 6. allowed-tools format
    allowed_tools = fm.get("allowed-tools")
    if allowed_tools is not None:
        if isinstance(allowed_tools, list):
            result.errors.append("allowed-tools must be space-delimited, not YAML array")
        else:
            tools_str = str(allowed_tools)
            if "," in tools_str:
                result.errors.append(f"allowed-tools must be space-delimited, not comma-separated: {tools_str}")
            elif "[" in tools_str or "]" in tools_str:
                result.errors.append(f"allowed-tools must be space-delimited, not array syntax: {tools_str}")

    # 7. Line count
    line_count = len(content.splitlines())
    if line_count > MAX_LINE_COUNT:
        result.errors.append(f"Line count exceeds {MAX_LINE_COUNT} (got {line_count})")
        return result

    # 8. Subdirectories
    if skill_dir.is_dir():
        for subdir in sorted(skill_dir.iterdir()):
            if not subdir.is_dir():
                continue
            subdir_name = subdir.name
            if subdir_name == "docs":
                result.warnings.append("docs/ is deprecated at skill level — use references/ (agentskills.io)")
            elif subdir_name in DEPRECATED_SUBDIRS:
                result.warnings.append("resources/ is deprecated — use references/, assets/, scripts/ instead")
            elif subdir_name not in ALLOWED_SUBDIRS:
                result.warnings.append(
                    f"Non-standard subdirectory: {subdir_name} (allowed: {', '.join(sorted(ALLOWED_SUBDIRS))})"
                )

    # 9-10. Content checks outside fenced code blocks
    prose = _strip_fenced_blocks(content)

    if ASCII_ART_PATTERN.search(prose):
        result.warnings.append("ASCII art detected outside code blocks - use plain lists or tables")

    if PERSONA_PATTERN.search(prose):
        result.errors.append("Persona statement detected ('You are a/an/the...') - use Audience/Goal framing")

    # 11. Description routing keywords
    if not ROUTING_KEYWORDS.search(desc):
        result.warnings.append("Description lacks routing keywords (use when, don't use, not for, triggers on)")

    # 12. Marketing buzzwords
    if MARKETING_BUZZWORDS.search(desc):
        result.warnings.append("Description contains marketing buzzwords - use precise, functional language")

    return result


def validate_skill_structure(skill_dir: Path) -> CheckResult:
    """Validate the real skill directory and retain diagnostics for Compass."""
    skill_path = skill_dir / "SKILL.md"
    result = validate_skill(skill_path)
    passed = not result.errors
    return CheckResult(
        check_id=CheckId.VALID_SKILL_STRUCTURE,
        name="Valid Skill Structure",
        passed=passed,
        score=1.0 if passed else 0.0,
        message="Skill structure validation passed" if passed else "Skill structure validation failed",
        source_gate="skill-structure-validation",
        details={
            "validator": "validate_skills_tier1",
            "skill_path": str(skill_path),
            "errors": result.errors,
            "warnings": result.warnings,
        },
    )


def load_skill_structure_result(report_path: Path) -> CheckResult:
    """Load the skill structure validation report for certification."""
    try:
        result = CheckResult.model_validate_json(report_path.read_text())
        if result.check_id != CheckId.VALID_SKILL_STRUCTURE:
            raise ValueError(f"Unexpected check_id: {result.check_id}")
        return result
    except (OSError, UnicodeError, ValidationError, ValueError) as exc:
        return CheckResult(
            check_id=CheckId.VALID_SKILL_STRUCTURE,
            name="Valid Skill Structure",
            passed=False,
            score=0.0,
            message="Skill structure validation result unavailable",
            source_gate="skill-structure-validation",
            details={"errors": [str(exc)], "warnings": []},
        )
