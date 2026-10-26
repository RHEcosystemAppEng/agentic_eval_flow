"""Tier 1 validation and the dedicated evidence consumed by certification."""

import pytest
import yaml

from abevalflow.certification import CheckId, compute_certification
from abevalflow.cli.main import main
from abevalflow.skill_structure import load_skill_structure_result, validate_skill_structure


@pytest.fixture
def skill_dir(tmp_path):
    path = tmp_path / "pack" / "skills" / "test-skill"
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: Use when testing\nlicense: Apache-2.0\n---\n# Test\n"
    )
    return path


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("name", None, "Missing required field: name"),
        ("name", "A" * 65, "Name length"),
        ("name", "Test-skill", "lowercase"),
        ("name", "test_skill", "underscores"),
        ("name", "other-skill", "does not match directory"),
        ("description", None, "Missing required field: description"),
        ("description", "x" * 1025, "Description exceeds"),
        ("license", None, "Missing required field: license"),
        ("license", "MIT", "License must be"),
        ("compatibility", "x" * 501, "Compatibility exceeds"),
        ("allowed-tools", ["Read", "Write"], "not YAML array"),
        ("allowed-tools", "Read, Write", "not comma-separated"),
        ("allowed-tools", "[Read Write]", "not array syntax"),
    ],
)
def test_frontmatter_rules(skill_dir, field, value, expected_error):
    frontmatter = {"name": "test-skill", "description": "Use when testing", "license": "Apache-2.0"}
    frontmatter[field] = value
    (skill_dir / "SKILL.md").write_text("---\n" + yaml.safe_dump(frontmatter) + "---\n# Test\n")

    result = validate_skill_structure(skill_dir)

    assert result.passed is False
    assert result.score == 0.0
    assert any(expected_error in error for error in result.details["errors"])


@pytest.mark.parametrize("content", ["# No frontmatter", "---\nname: x", "---\n[invalid\n---", "---\n- item\n---"])
def test_malformed_frontmatter_is_a_validation_failure(skill_dir, content):
    (skill_dir / "SKILL.md").write_text(content)
    assert validate_skill_structure(skill_dir).passed is False


def test_missing_or_unreadable_skill_is_a_validation_failure(skill_dir):
    (skill_dir / "SKILL.md").unlink()
    assert "Cannot read SKILL.md" in validate_skill_structure(skill_dir).details["errors"][0]
    (skill_dir / "SKILL.md").write_bytes(b"\xff")
    assert validate_skill_structure(skill_dir).passed is False


def test_line_limit_and_persona_rules(skill_dir):
    path = skill_dir / "SKILL.md"
    original = path.read_text()
    path.write_text(original + "\n" * 501)
    assert "Line count exceeds" in validate_skill_structure(skill_dir).details["errors"][0]
    path.write_text(original + "You are an expert.\n")
    assert "Persona statement" in validate_skill_structure(skill_dir).details["errors"][0]
    path.write_text(original + "```\nYou are an expert.\n```\n")
    assert validate_skill_structure(skill_dir).passed is True


def test_warnings_remain_advisory(skill_dir):
    path = skill_dir / "SKILL.md"
    path.write_text(path.read_text().replace("Use when testing", "Powerful testing") + "───\n")
    for directory in ("docs", "resources", "other", "scripts", "references", "assets"):
        (skill_dir / directory).mkdir()

    result = validate_skill_structure(skill_dir)

    assert result.passed is True
    assert result.score == 1.0
    assert result.details["errors"] == []
    assert len(result.details["warnings"]) == 6


@pytest.mark.parametrize("valid_skill", [True, False])
def test_cli_report_drives_scorecard_independently_of_submission(skill_dir, tmp_path, valid_skill):
    reports_dir = tmp_path / "reports"
    report_path = reports_dir / "skill-structure-validation.json"
    if not valid_skill:
        (skill_dir / "SKILL.md").write_text("# Missing frontmatter\n")
    assert main(["validate", "skill", str(skill_dir), "--output", str(report_path)]) == (0 if valid_skill else 1)
    certification = compute_certification(
        gates=[], validation_passed=True, skill_structure_result=load_skill_structure_result(report_path)
    )

    check = next(
        check for check in certification.foundational.checks if check.check_id == CheckId.VALID_SKILL_STRUCTURE
    )
    assert check.passed is valid_skill
    assert check.details["skill_path"] == str(skill_dir / "SKILL.md")
    assert bool(check.details["errors"]) is not valid_skill


@pytest.mark.parametrize(
    "content",
    [None, "{", "{}", '{"check_id": "metadata_compliance", "name": "Metadata", "passed": true}'],
)
def test_missing_or_invalid_structure_evidence_fails(tmp_path, content):
    report = tmp_path / "skill-structure-validation.json"
    if content is not None:
        report.write_text(content)
    check = load_skill_structure_result(report)
    result = compute_certification(gates=[], validation_passed=True, skill_structure_result=check)
    assert result.foundational.checks[0].passed is False
    assert "unavailable" in result.foundational.checks[0].message
