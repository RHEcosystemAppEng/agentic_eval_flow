"""Tests for certification level computation."""

from __future__ import annotations

import pytest

from abevalflow.certification import (
    CERTIFIED_CHECKS,
    DEFAULT_THRESHOLDS,
    FOUNDATIONAL_CHECKS,
    TRUSTED_CHECKS,
    CertificationLevel,
    CertificationResult,
    CheckId,
    CheckResult,
    LevelResult,
    _validate_check_ids,
    compute_certification,
)
from abevalflow.gates.base import Finding, GateMode, GateResult, GateType, Severity
from abevalflow.schemas import CertificationLevelPolicy, CertificationPolicy


@pytest.fixture
def passing_structure_result():
    return CheckResult(
        check_id=CheckId.VALID_SKILL_STRUCTURE,
        name="Valid Skill Structure",
        passed=True,
        score=1.0,
    )


class TestCheckResult:
    """Tests for CheckResult model."""

    def test_basic_check_result(self) -> None:
        result = CheckResult(
            check_id=CheckId.VALID_SKILL_STRUCTURE,
            name="Valid Skill Structure",
            passed=True,
            score=1.0,
            message="All checks passed",
        )
        assert result.passed is True
        assert result.score == 1.0

    def test_failed_check_result(self) -> None:
        result = CheckResult(
            check_id=CheckId.BASIC_SECURITY_VALIDATION,
            name="Basic Security Validation",
            passed=False,
            score=0.3,
            message="High severity findings detected",
            source_gate="security",
        )
        assert result.passed is False
        assert result.source_gate == "security"


class TestLevelResult:
    """Tests for LevelResult model."""

    def test_all_checks_passed(self) -> None:
        checks = [
            CheckResult(
                check_id=CheckId.VALID_SKILL_STRUCTURE,
                name="Test 1",
                passed=True,
                score=1.0,
            ),
            CheckResult(
                check_id=CheckId.BASIC_SECURITY_VALIDATION,
                name="Test 2",
                passed=True,
                score=0.8,
            ),
        ]
        level = LevelResult(
            level=CertificationLevel.FOUNDATIONAL,
            passed=True,
            checks=checks,
        )
        assert level.checks_passed == 2
        assert level.checks_total == 2
        assert level.overall_score == 0.9

    def test_some_checks_failed(self) -> None:
        checks = [
            CheckResult(
                check_id=CheckId.VALID_SKILL_STRUCTURE,
                name="Test 1",
                passed=True,
                score=1.0,
            ),
            CheckResult(
                check_id=CheckId.BASIC_SECURITY_VALIDATION,
                name="Test 2",
                passed=False,
                score=0.4,
            ),
        ]
        level = LevelResult(
            level=CertificationLevel.FOUNDATIONAL,
            passed=False,
            checks=checks,
        )
        assert level.checks_passed == 1
        assert level.checks_total == 2
        assert level.overall_score == 0.7


class TestCertificationResult:
    """Tests for CertificationResult model."""

    def test_highest_level_certified(self) -> None:
        result = CertificationResult(
            foundational=LevelResult(level=CertificationLevel.FOUNDATIONAL, passed=True),
            trusted=LevelResult(level=CertificationLevel.TRUSTED, passed=True),
            certified=LevelResult(level=CertificationLevel.CERTIFIED, passed=True),
        )
        assert result.highest_level == CertificationLevel.CERTIFIED

    def test_highest_level_trusted(self) -> None:
        result = CertificationResult(
            foundational=LevelResult(level=CertificationLevel.FOUNDATIONAL, passed=True),
            trusted=LevelResult(level=CertificationLevel.TRUSTED, passed=True),
            certified=LevelResult(level=CertificationLevel.CERTIFIED, passed=False),
        )
        assert result.highest_level == CertificationLevel.TRUSTED

    def test_highest_level_foundational(self) -> None:
        result = CertificationResult(
            foundational=LevelResult(level=CertificationLevel.FOUNDATIONAL, passed=True),
            trusted=LevelResult(level=CertificationLevel.TRUSTED, passed=False),
            certified=LevelResult(level=CertificationLevel.CERTIFIED, passed=False),
        )
        assert result.highest_level == CertificationLevel.FOUNDATIONAL

    def test_highest_level_none(self) -> None:
        result = CertificationResult(
            foundational=LevelResult(level=CertificationLevel.FOUNDATIONAL, passed=False),
            trusted=LevelResult(level=CertificationLevel.TRUSTED, passed=False),
            certified=LevelResult(level=CertificationLevel.CERTIFIED, passed=False),
        )
        assert result.highest_level == CertificationLevel.NONE

    def test_hierarchical_requirement_certified_needs_trusted(self) -> None:
        result = CertificationResult(
            foundational=LevelResult(level=CertificationLevel.FOUNDATIONAL, passed=True),
            trusted=LevelResult(level=CertificationLevel.TRUSTED, passed=False),
            certified=LevelResult(level=CertificationLevel.CERTIFIED, passed=True),
        )
        assert result.highest_level == CertificationLevel.FOUNDATIONAL

    def test_get_level_result(self) -> None:
        foundational = LevelResult(level=CertificationLevel.FOUNDATIONAL, passed=True)
        trusted = LevelResult(level=CertificationLevel.TRUSTED, passed=False)
        certified = LevelResult(level=CertificationLevel.CERTIFIED, passed=False)
        result = CertificationResult(
            foundational=foundational,
            trusted=trusted,
            certified=certified,
        )
        assert result.get_level_result(CertificationLevel.FOUNDATIONAL) == foundational
        assert result.get_level_result(CertificationLevel.TRUSTED) == trusted
        assert result.get_level_result(CertificationLevel.CERTIFIED) == certified
        assert result.get_level_result(CertificationLevel.NONE) is None


class TestComputeCertification:
    """Tests for compute_certification function."""

    def test_no_gates_minimal_certification(self) -> None:
        result = compute_certification(
            gates=[],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        assert result.foundational.passed is False
        assert result.highest_level == CertificationLevel.NONE

    def test_engine_gate_provides_execution_checks(self) -> None:
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.85,
            mode=GateMode.BLOCK,
            message="85% pass rate",
        )
        result = compute_certification(
            gates=[engine_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        foundational_check_ids = [c.check_id for c in result.foundational.checks]
        assert CheckId.BASIC_EXECUTION_VALIDATION in foundational_check_ids

        trusted_check_ids = [c.check_id for c in result.trusted.checks]
        assert CheckId.FUNCTIONAL_VALIDATION in trusted_check_ids

    def test_security_gate_provides_security_checks(self) -> None:
        security_gate = GateResult(
            gate_name="security",
            gate_type=GateType.SECURITY,
            passed=True,
            score=0.95,
            mode=GateMode.BLOCK,
            message="No critical findings",
            findings=[],
        )
        result = compute_certification(
            gates=[security_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        foundational_check_ids = [c.check_id for c in result.foundational.checks]
        assert CheckId.BASIC_SECURITY_VALIDATION in foundational_check_ids

        trusted_check_ids = [c.check_id for c in result.trusted.checks]
        assert CheckId.ADVANCED_SECURITY_VALIDATION in trusted_check_ids

    def test_quality_gate_provides_quality_checks(self) -> None:
        quality_gate = GateResult(
            gate_name="quality",
            gate_type=GateType.QUALITY,
            passed=True,
            score=0.8,
            mode=GateMode.WARN,
            message="Quality review passed",
        )
        result = compute_certification(
            gates=[quality_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        foundational_check_ids = [c.check_id for c in result.foundational.checks]
        assert CheckId.CONTENT_QUALITY_REVIEW in foundational_check_ids

    def test_missing_skill_validation_fails_despite_submission_pass(self) -> None:
        result = compute_certification(
            gates=[],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        valid_structure_check = next(
            c for c in result.foundational.checks if c.check_id == CheckId.VALID_SKILL_STRUCTURE
        )
        assert valid_structure_check.passed is False

    def test_missing_metadata_affects_certification(self) -> None:
        result = compute_certification(
            gates=[],
            validation_passed=True,
            metadata_valid=False,
            has_eval_assets=True,
        )
        metadata_check = next(c for c in result.foundational.checks if c.check_id == CheckId.METADATA_COMPLIANCE)
        assert metadata_check.passed is False

    def test_missing_eval_assets_affects_trusted(self) -> None:
        result = compute_certification(
            gates=[],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=False,
        )
        eval_assets_check = next(c for c in result.trusted.checks if c.check_id == CheckId.EVALUATION_ASSETS)
        assert eval_assets_check.passed is False

    def test_high_score_engine_provides_advanced_validation(self) -> None:
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.90,
            mode=GateMode.BLOCK,
        )
        result = compute_certification(
            gates=[engine_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        advanced_check = next(c for c in result.certified.checks if c.check_id == CheckId.ADVANCED_AGENT_VALIDATION)
        assert advanced_check.passed is True

    def test_low_score_engine_fails_advanced_validation(self) -> None:
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.60,
            mode=GateMode.BLOCK,
        )
        result = compute_certification(
            gates=[engine_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        advanced_check = next(c for c in result.certified.checks if c.check_id == CheckId.ADVANCED_AGENT_VALIDATION)
        assert advanced_check.passed is False

    def test_security_findings_affect_enterprise_review(self) -> None:
        findings = [
            Finding(
                rule_id="test-rule",
                severity=Severity.HIGH,
                message="Test finding",
            )
        ]
        security_gate = GateResult(
            gate_name="security",
            gate_type=GateType.SECURITY,
            passed=False,
            score=0.5,
            mode=GateMode.BLOCK,
            findings=findings,
        )
        result = compute_certification(
            gates=[security_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        enterprise_check = next(c for c in result.certified.checks if c.check_id == CheckId.ENTERPRISE_SECURITY_REVIEW)
        assert enterprise_check.passed is False

    def test_default_checks_are_valid_check_ids(self) -> None:
        """Verify all default checks are valid CheckId values."""
        all_level_checks = set(FOUNDATIONAL_CHECKS + TRUSTED_CHECKS + CERTIFIED_CHECKS)
        all_check_ids = set(CheckId)
        # All default checks should be valid CheckIds (subset)
        assert all_level_checks.issubset(all_check_ids)


class TestCertificationLevelConstants:
    """Tests for certification level check constants."""

    def test_foundational_has_expected_checks(self) -> None:
        assert CheckId.VALID_SKILL_STRUCTURE in FOUNDATIONAL_CHECKS
        assert CheckId.BASIC_SECURITY_VALIDATION in FOUNDATIONAL_CHECKS
        assert CheckId.BASIC_EXECUTION_VALIDATION in FOUNDATIONAL_CHECKS
        assert CheckId.CONTENT_QUALITY_REVIEW in FOUNDATIONAL_CHECKS
        assert CheckId.METADATA_COMPLIANCE in FOUNDATIONAL_CHECKS
        assert len(FOUNDATIONAL_CHECKS) == 5

    def test_trusted_has_expected_checks(self) -> None:
        assert CheckId.EVALUATION_ASSETS in TRUSTED_CHECKS
        assert CheckId.ADVANCED_SECURITY_VALIDATION in TRUSTED_CHECKS
        assert CheckId.FUNCTIONAL_VALIDATION in TRUSTED_CHECKS
        assert CheckId.INSTRUCTION_QUALITY in TRUSTED_CHECKS
        # registry_governance not yet implemented
        assert len(TRUSTED_CHECKS) == 5

    def test_certified_has_expected_checks(self) -> None:
        assert CheckId.ENTERPRISE_STRUCTURE_VALIDATION in CERTIFIED_CHECKS
        assert CheckId.ENTERPRISE_SECURITY_REVIEW in CERTIFIED_CHECKS
        assert CheckId.ENTERPRISE_BEHAVIORAL_TESTING in CERTIFIED_CHECKS
        assert CheckId.ADVANCED_AGENT_VALIDATION in CERTIFIED_CHECKS
        # continuous_optimization not yet implemented
        assert len(CERTIFIED_CHECKS) == 4


class TestCertificationPolicy:
    """Tests for CertificationPolicy configuration."""

    def test_empty_policy_uses_defaults(self) -> None:
        """Empty policy should use default checks for all levels."""
        policy = CertificationPolicy()
        result = compute_certification(
            gates=[],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
            policy=policy,
        )
        assert len(result.foundational.checks) == len(FOUNDATIONAL_CHECKS)
        assert len(result.trusted.checks) == len(TRUSTED_CHECKS)
        assert len(result.certified.checks) == len(CERTIFIED_CHECKS)

    def test_none_policy_uses_defaults(self) -> None:
        """None policy should use default checks for all levels."""
        result = compute_certification(
            gates=[],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
            policy=None,
        )
        assert len(result.foundational.checks) == len(FOUNDATIONAL_CHECKS)
        assert len(result.trusted.checks) == len(TRUSTED_CHECKS)
        assert len(result.certified.checks) == len(CERTIFIED_CHECKS)

    def test_custom_checks_for_foundational(self) -> None:
        """Custom check list for foundational level."""
        policy = CertificationPolicy(
            foundational=CertificationLevelPolicy(
                checks=[
                    "valid_skill_structure",
                    "basic_security_validation",
                ]
            )
        )
        result = compute_certification(
            gates=[],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
            policy=policy,
        )
        assert len(result.foundational.checks) == 2
        check_ids = [c.check_id for c in result.foundational.checks]
        assert CheckId.VALID_SKILL_STRUCTURE in check_ids
        assert CheckId.BASIC_SECURITY_VALIDATION in check_ids
        assert CheckId.METADATA_COMPLIANCE not in check_ids

    def test_custom_checks_for_certified(self) -> None:
        """Custom check list for certified level respects hierarchy.

        Even if certified's own checks pass, certified.passed is False
        if foundational or trusted fail (hierarchy enforcement).
        """
        policy = CertificationPolicy(certified=CertificationLevelPolicy(checks=["advanced_agent_validation"]))
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.85,
            mode=GateMode.BLOCK,
        )
        result = compute_certification(
            gates=[engine_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
            policy=policy,
        )
        # Certified's own check passes
        assert len(result.certified.checks) == 1
        assert result.certified.checks[0].check_id == CheckId.ADVANCED_AGENT_VALIDATION
        assert result.certified.checks[0].passed is True
        # But certified.passed is False due to hierarchy (foundational failed)
        assert result.certified.passed is False
        assert result.foundational.passed is False  # missing security/quality gates

    def test_threshold_override_for_advanced_agent_validation(self) -> None:
        """Override threshold for advanced_agent_validation."""
        policy = CertificationPolicy(certified=CertificationLevelPolicy(thresholds={"advanced_agent_validation": 0.5}))
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.6,
            mode=GateMode.BLOCK,
        )
        result = compute_certification(
            gates=[engine_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
            policy=policy,
        )
        advanced_check = next(c for c in result.certified.checks if c.check_id == CheckId.ADVANCED_AGENT_VALIDATION)
        assert advanced_check.passed is True

    def test_threshold_override_fails_when_below(self) -> None:
        """Score below overridden threshold should fail."""
        policy = CertificationPolicy(certified=CertificationLevelPolicy(thresholds={"advanced_agent_validation": 0.9}))
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.85,
            mode=GateMode.BLOCK,
        )
        result = compute_certification(
            gates=[engine_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
            policy=policy,
        )
        advanced_check = next(c for c in result.certified.checks if c.check_id == CheckId.ADVANCED_AGENT_VALIDATION)
        assert advanced_check.passed is False
        assert "0.90" in advanced_check.message

    def test_partial_policy_only_foundational(self, passing_structure_result) -> None:
        """Partial policy with only foundational specified."""
        policy = CertificationPolicy(
            foundational=CertificationLevelPolicy(
                checks=[
                    "valid_skill_structure",
                    "metadata_compliance",
                ]
            )
        )
        result = compute_certification(
            gates=[],
            validation_passed=True,
            skill_structure_result=passing_structure_result,
            metadata_valid=True,
            has_eval_assets=True,
            policy=policy,
        )
        assert len(result.foundational.checks) == 2
        assert result.foundational.passed is True
        assert len(result.trusted.checks) == len(TRUSTED_CHECKS)
        assert len(result.certified.checks) == len(CERTIFIED_CHECKS)

    def test_invalid_check_id_raises_error(self) -> None:
        """Invalid check ID should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            _validate_check_ids(["invalid_check_id"])
        assert "Invalid check ID 'invalid_check_id'" in str(exc_info.value)

    def test_threshold_override_for_instruction_quality(self) -> None:
        """Override threshold for instruction_quality check."""
        policy = CertificationPolicy(trusted=CertificationLevelPolicy(thresholds={"instruction_quality": 0.5}))
        quality_gate = GateResult(
            gate_name="quality",
            gate_type=GateType.QUALITY,
            passed=True,
            score=0.6,
            mode=GateMode.WARN,
        )
        result = compute_certification(
            gates=[quality_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
            policy=policy,
        )
        instruction_check = next(c for c in result.trusted.checks if c.check_id == CheckId.INSTRUCTION_QUALITY)
        assert instruction_check.passed is True

    def test_threshold_override_for_advanced_security(self) -> None:
        """Override threshold for advanced_security_validation check."""
        policy = CertificationPolicy(trusted=CertificationLevelPolicy(thresholds={"advanced_security_validation": 0.7}))
        security_gate = GateResult(
            gate_name="security",
            gate_type=GateType.SECURITY,
            passed=True,
            score=0.75,
            mode=GateMode.BLOCK,
            findings=[],
        )
        result = compute_certification(
            gates=[security_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
            policy=policy,
        )
        adv_security_check = next(
            c for c in result.trusted.checks if c.check_id == CheckId.ADVANCED_SECURITY_VALIDATION
        )
        assert adv_security_check.passed is True

    def test_custom_checks_empty_list(self) -> None:
        """Empty check list should fail the level (no checks = cannot pass)."""
        policy = CertificationPolicy(foundational=CertificationLevelPolicy(checks=[]))
        result = compute_certification(
            gates=[],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
            policy=policy,
        )
        assert len(result.foundational.checks) == 0
        # Empty check list should NOT pass - certification requires actual checks
        assert result.foundational.passed is False

    def test_combined_custom_checks_and_thresholds(self) -> None:
        """Custom checks and thresholds together."""
        policy = CertificationPolicy(
            foundational=CertificationLevelPolicy(
                checks=["basic_execution_validation"],
                thresholds={"basic_execution_validation": 0.3},
            )
        )
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.4,
            mode=GateMode.BLOCK,
        )
        result = compute_certification(
            gates=[engine_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
            policy=policy,
        )
        assert len(result.foundational.checks) == 1
        assert result.foundational.checks[0].check_id == CheckId.BASIC_EXECUTION_VALIDATION
        assert result.foundational.passed is True


class TestDefaultThresholds:
    """Tests for default threshold constants."""

    def test_default_thresholds_exist(self) -> None:
        """Verify default thresholds are defined."""
        assert CheckId.ADVANCED_AGENT_VALIDATION in DEFAULT_THRESHOLDS
        assert CheckId.ADVANCED_SECURITY_VALIDATION in DEFAULT_THRESHOLDS
        assert CheckId.INSTRUCTION_QUALITY in DEFAULT_THRESHOLDS

    def test_default_threshold_values(self) -> None:
        """Verify default threshold values."""
        assert DEFAULT_THRESHOLDS[CheckId.ADVANCED_AGENT_VALIDATION] == 0.8
        assert DEFAULT_THRESHOLDS[CheckId.ADVANCED_SECURITY_VALIDATION] == 0.9
        assert DEFAULT_THRESHOLDS[CheckId.INSTRUCTION_QUALITY] == 0.7


class TestInvalidThresholdKeyValidation:
    """Tests for threshold key validation in CertificationLevelPolicy."""

    def test_invalid_threshold_key_raises_error(self) -> None:
        """Invalid threshold key should raise ValueError during validation."""
        with pytest.raises(ValueError) as exc_info:
            CertificationLevelPolicy(thresholds={"invalid_check_name": 0.5})
        assert "Invalid threshold key 'invalid_check_name'" in str(exc_info.value)

    def test_valid_threshold_key_passes(self) -> None:
        """Valid threshold key should pass validation."""
        policy = CertificationLevelPolicy(thresholds={"advanced_agent_validation": 0.5})
        assert policy.thresholds["advanced_agent_validation"] == 0.5

    def test_multiple_valid_threshold_keys(self) -> None:
        """Multiple valid threshold keys should pass."""
        policy = CertificationLevelPolicy(
            thresholds={
                "advanced_agent_validation": 0.5,
                "instruction_quality": 0.6,
                "advanced_security_validation": 0.8,
            }
        )
        assert len(policy.thresholds) == 3

    def test_mixed_valid_and_invalid_keys_raises(self) -> None:
        """Mix of valid and invalid keys should raise for the invalid one."""
        with pytest.raises(ValueError) as exc_info:
            CertificationLevelPolicy(
                thresholds={
                    "advanced_agent_validation": 0.5,
                    "typo_in_check_name": 0.6,
                }
            )
        assert "typo_in_check_name" in str(exc_info.value)


class TestEnterpriseSecurityReviewMessage:
    """Tests for ENTERPRISE_SECURITY_REVIEW message logic."""

    def test_message_when_score_below_threshold_no_findings(self) -> None:
        """Message should mention score when below threshold with no findings."""
        security_gate = GateResult(
            gate_name="security",
            gate_type=GateType.SECURITY,
            passed=True,
            score=0.5,
            mode=GateMode.BLOCK,
            findings=[],
        )
        result = compute_certification(
            gates=[security_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        enterprise_check = next(c for c in result.certified.checks if c.check_id == CheckId.ENTERPRISE_SECURITY_REVIEW)
        assert enterprise_check.passed is False
        assert "Score" in enterprise_check.message
        assert "below threshold" in enterprise_check.message

    def test_message_when_score_high_no_findings(self) -> None:
        """Message should say 'No security findings' when score high and no findings."""
        security_gate = GateResult(
            gate_name="security",
            gate_type=GateType.SECURITY,
            passed=True,
            score=0.95,
            mode=GateMode.BLOCK,
            findings=[],
        )
        result = compute_certification(
            gates=[security_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        enterprise_check = next(c for c in result.certified.checks if c.check_id == CheckId.ENTERPRISE_SECURITY_REVIEW)
        assert enterprise_check.passed is True
        assert enterprise_check.message == "No security findings"

    def test_message_when_findings_present(self) -> None:
        """Message should mention findings count when findings present."""
        findings = [
            Finding(rule_id="test-1", severity=Severity.MEDIUM, message="Finding 1"),
            Finding(rule_id="test-2", severity=Severity.LOW, message="Finding 2"),
        ]
        security_gate = GateResult(
            gate_name="security",
            gate_type=GateType.SECURITY,
            passed=True,
            score=0.8,
            mode=GateMode.BLOCK,
            findings=findings,
        )
        result = compute_certification(
            gates=[security_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        enterprise_check = next(c for c in result.certified.checks if c.check_id == CheckId.ENTERPRISE_SECURITY_REVIEW)
        assert enterprise_check.passed is False
        assert "2 findings require review" in enterprise_check.message


class TestCertificationPolicyGetThreshold:
    """Tests for CertificationPolicy.get_threshold semantics."""

    def test_get_threshold_last_wins(self) -> None:
        """Later level threshold should override earlier level."""
        policy = CertificationPolicy(
            foundational=CertificationLevelPolicy(thresholds={"advanced_agent_validation": 0.5}),
            certified=CertificationLevelPolicy(thresholds={"advanced_agent_validation": 0.9}),
        )
        assert policy.get_threshold("advanced_agent_validation") == 0.9

    def test_get_threshold_returns_none_when_not_set(self) -> None:
        """Should return None when threshold not configured."""
        policy = CertificationPolicy()
        assert policy.get_threshold("advanced_agent_validation") is None

    def test_get_threshold_from_single_level(self) -> None:
        """Should return threshold from whichever level defines it."""
        policy = CertificationPolicy(
            trusted=CertificationLevelPolicy(thresholds={"instruction_quality": 0.6}),
        )
        assert policy.get_threshold("instruction_quality") == 0.6


class TestBothModeCheckMerging:
    """Tests for 'both' mode with multiple engines producing the same check."""

    def test_both_mode_keeps_failing_check_conservative(self) -> None:
        """When both engines produce the same check, keep the failing one."""
        harbor_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            policy_key="harbor",
            passed=True,
            score=0.85,
            mode=GateMode.BLOCK,
            message="Harbor passed",
        )
        ase_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            policy_key="ase",
            passed=False,
            score=0.45,
            mode=GateMode.BLOCK,
            message="AEH failed",
        )
        result = compute_certification(
            gates=[harbor_gate, ase_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        exec_check = next(c for c in result.foundational.checks if c.check_id == CheckId.BASIC_EXECUTION_VALIDATION)
        assert exec_check.passed is False
        assert exec_check.details.get("source_implementation") == "ase"

    def test_both_mode_passing_first_failing_second(self) -> None:
        """Failing check from second engine should overwrite passing from first."""
        harbor_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            policy_key="harbor",
            passed=True,
            score=0.9,
            mode=GateMode.BLOCK,
        )
        ase_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            policy_key="ase",
            passed=False,
            score=0.3,
            mode=GateMode.BLOCK,
        )
        result = compute_certification(
            gates=[harbor_gate, ase_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        func_check = next(c for c in result.trusted.checks if c.check_id == CheckId.FUNCTIONAL_VALIDATION)
        assert func_check.passed is False
        assert func_check.details.get("source_implementation") == "ase"

    def test_both_mode_failing_first_passing_second(self) -> None:
        """Passing check from second engine should NOT overwrite failing from first."""
        harbor_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            policy_key="harbor",
            passed=False,
            score=0.3,
            mode=GateMode.BLOCK,
        )
        ase_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            policy_key="ase",
            passed=True,
            score=0.9,
            mode=GateMode.BLOCK,
        )
        result = compute_certification(
            gates=[harbor_gate, ase_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        exec_check = next(c for c in result.foundational.checks if c.check_id == CheckId.BASIC_EXECUTION_VALIDATION)
        assert exec_check.passed is False
        assert exec_check.details.get("source_implementation") == "harbor"

    def test_check_result_includes_source_implementation(self) -> None:
        """CheckResult should include source_implementation in details."""
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            policy_key="harbor",
            passed=True,
            score=0.85,
            mode=GateMode.BLOCK,
        )
        result = compute_certification(
            gates=[engine_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
        )
        exec_check = next(c for c in result.foundational.checks if c.check_id == CheckId.BASIC_EXECUTION_VALIDATION)
        assert "source_implementation" in exec_check.details
        assert exec_check.details["source_implementation"] == "harbor"


class TestHierarchyEnforcement:
    """Tests for certification hierarchy enforcement in compute_certification.

    The hierarchy is: Foundational < Trusted < Certified
    If a lower level fails, higher levels must also fail even if their own checks pass.
    """

    def test_trusted_fails_certified_cascades(self, passing_structure_result) -> None:
        """If trusted's own checks fail, certified should also fail.

        Scenario: foundational passes, trusted fails its own checks
        Expected: certified.passed should be False
        """
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.85,
            mode=GateMode.BLOCK,
        )
        # Use simplified policy: foundational needs only structure checks,
        # trusted needs functional_validation which will pass from engine gate,
        # BUT also needs advanced_security_validation which won't be satisfied
        policy = CertificationPolicy(
            foundational=CertificationLevelPolicy(checks=["valid_skill_structure", "metadata_compliance"]),
            trusted=CertificationLevelPolicy(checks=["functional_validation", "advanced_security_validation"]),
            certified=CertificationLevelPolicy(checks=["advanced_agent_validation"]),
        )

        result = compute_certification(
            gates=[engine_gate],
            validation_passed=True,
            skill_structure_result=passing_structure_result,
            metadata_valid=True,
            has_eval_assets=True,
            policy=policy,
        )

        # Foundational should pass (only needs structure/metadata)
        assert result.foundational.passed is True
        # Trusted should fail (advanced_security_validation not satisfied)
        assert result.trusted.passed is False
        # Certified should also fail due to hierarchy
        assert result.certified.passed is False
        assert result.highest_level == CertificationLevel.FOUNDATIONAL

    def test_foundational_fails_trusted_cascades(self) -> None:
        """If foundational fails, trusted should also fail even if its own checks pass.

        Scenario: foundational fails (validation_passed=False), provide gates for trusted
        Expected: trusted.passed should be False despite its checks being satisfied
        """
        # Provide gates for trusted-level checks
        security_gate = GateResult(
            gate_name="security",
            gate_type=GateType.SECURITY,
            passed=True,
            score=1.0,
            mode=GateMode.BLOCK,
        )
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.85,
            mode=GateMode.BLOCK,
        )

        result = compute_certification(
            gates=[security_gate, engine_gate],
            validation_passed=False,  # This causes foundational to fail
            metadata_valid=True,
            has_eval_assets=True,
        )

        # Foundational fails because validation_passed=False
        assert result.foundational.passed is False
        # Trusted should fail due to hierarchy even if its checks might pass
        assert result.trusted.passed is False
        # Certified should also fail
        assert result.certified.passed is False
        assert result.highest_level == CertificationLevel.NONE

    def test_foundational_fails_trusted_checks_still_preserved(self) -> None:
        """When hierarchy forces trusted to fail, individual check results are preserved.

        This allows consumers to see what would have passed if foundational passed.
        """
        # Provide gates that satisfy trusted checks
        security_gate = GateResult(
            gate_name="security",
            gate_type=GateType.SECURITY,
            passed=True,
            score=1.0,
            mode=GateMode.BLOCK,
        )
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.85,
            mode=GateMode.BLOCK,
        )

        result = compute_certification(
            gates=[security_gate, engine_gate],
            validation_passed=False,  # Causes foundational to fail
            metadata_valid=True,
            has_eval_assets=True,
        )

        # Trusted level fails due to hierarchy
        assert result.trusted.passed is False
        # But individual checks that could be evaluated are still present
        func_check = next((c for c in result.trusted.checks if c.check_id == CheckId.FUNCTIONAL_VALIDATION), None)
        assert func_check is not None
        # The check itself passed (from the engine gate)
        assert func_check.passed is True

    def test_all_levels_pass_when_all_requirements_met(self, passing_structure_result) -> None:
        """All levels pass when all their requirements are satisfied."""
        security_gate = GateResult(
            gate_name="security",
            gate_type=GateType.SECURITY,
            passed=True,
            score=1.0,
            mode=GateMode.BLOCK,
        )
        quality_gate = GateResult(
            gate_name="quality",
            gate_type=GateType.QUALITY,
            passed=True,
            score=0.9,
            mode=GateMode.BLOCK,
        )
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.85,
            mode=GateMode.BLOCK,
        )
        # Use policy to simplify check requirements
        policy = CertificationPolicy(
            foundational=CertificationLevelPolicy(checks=["valid_skill_structure", "metadata_compliance"]),
            trusted=CertificationLevelPolicy(checks=["functional_validation"]),
            certified=CertificationLevelPolicy(checks=["advanced_agent_validation"]),
        )

        result = compute_certification(
            gates=[security_gate, quality_gate, engine_gate],
            validation_passed=True,
            skill_structure_result=passing_structure_result,
            metadata_valid=True,
            has_eval_assets=True,
            policy=policy,
        )

        assert result.foundational.passed is True
        assert result.trusted.passed is True
        assert result.certified.passed is True
        assert result.highest_level == CertificationLevel.CERTIFIED


class TestProfileLoading:
    """Tests for certification profile loading from YAML."""

    def test_load_profile_skill(self) -> None:
        """Load the default 'skill' profile."""
        from abevalflow.certification import clear_profiles_cache, load_profile

        clear_profiles_cache()
        policy = load_profile("skill")

        assert policy is not None
        assert policy.foundational is not None
        assert "valid_skill_structure" in policy.foundational.checks
        assert "basic_execution_validation" in policy.foundational.checks

        # safety_toxicity_bias_guardrails not yet implemented, commented out in profile

    def test_load_profile_invalid_raises(self) -> None:
        """Loading a non-existent profile raises ValueError."""
        from abevalflow.certification import clear_profiles_cache, load_profile

        clear_profiles_cache()
        with pytest.raises(ValueError, match="Unknown certification profile"):
            load_profile("nonexistent_profile")

    def test_get_available_profiles(self) -> None:
        """Get list of available profile names."""
        from abevalflow.certification import clear_profiles_cache, get_available_profiles

        clear_profiles_cache()
        profiles = get_available_profiles()

        assert "skill" in profiles
        assert profiles == ["skill"]

    def test_get_default_profile_name(self) -> None:
        """Get the default profile name from configuration."""
        from abevalflow.certification import clear_profiles_cache, get_default_profile_name

        clear_profiles_cache()
        default = get_default_profile_name()

        assert default == "skill"

    def test_load_profile_none_uses_default(self) -> None:
        """Loading with None uses the default profile."""
        from abevalflow.certification import clear_profiles_cache, load_profile

        clear_profiles_cache()
        policy = load_profile(None)

        # Should load 'skill' profile (the default)
        assert policy is not None
        assert policy.foundational is not None
        assert "valid_skill_structure" in policy.foundational.checks

    def test_profile_integrates_with_compute_certification(self) -> None:
        """Profile-loaded policy works with compute_certification."""
        from abevalflow.certification import clear_profiles_cache, load_profile

        clear_profiles_cache()
        policy = load_profile("skill")

        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.9,
            mode=GateMode.BLOCK,
        )

        result = compute_certification(
            gates=[engine_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
            policy=policy,
        )

        # Should use skill profile's check configuration
        assert result is not None
        assert result.foundational is not None


class TestOperationalPolicyCertificationIntegration:
    def test_passing_check_enables_trusted(self, passing_structure_result) -> None:
        passing_result = CheckResult(
            check_id=CheckId.OPERATIONAL_POLICY_COMPLIANCE,
            name="Operational Policy Compliance",
            passed=True,
            score=1.0,
            message="All passed",
        )
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.9,
            mode=GateMode.BLOCK,
            message="Pass",
        )
        security_gate = GateResult(
            gate_name="security",
            gate_type=GateType.SECURITY,
            passed=True,
            score=1.0,
            mode=GateMode.BLOCK,
            message="Pass",
            findings=[],
        )
        quality_gate = GateResult(
            gate_name="quality",
            gate_type=GateType.QUALITY,
            passed=True,
            score=0.8,
            mode=GateMode.WARN,
            message="Pass",
        )
        result = compute_certification(
            gates=[engine_gate, security_gate, quality_gate],
            validation_passed=True,
            skill_structure_result=passing_structure_result,
            metadata_valid=True,
            has_eval_assets=True,
            operational_policy_result=passing_result,
        )
        assert result.trusted.passed is True
        op_check = next(c for c in result.trusted.checks if c.check_id == CheckId.OPERATIONAL_POLICY_COMPLIANCE)
        assert op_check.passed is True

    def test_failing_check_blocks_trusted(self) -> None:
        failing_result = CheckResult(
            check_id=CheckId.OPERATIONAL_POLICY_COMPLIANCE,
            name="Operational Policy Compliance",
            passed=False,
            score=0.25,
            message="CPU exceeds limit",
        )
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.9,
            mode=GateMode.BLOCK,
            message="Pass",
        )
        result = compute_certification(
            gates=[engine_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
            operational_policy_result=failing_result,
        )
        assert result.trusted.passed is False

    def test_none_result_uses_default_not_implemented(self) -> None:
        engine_gate = GateResult(
            gate_name="evaluation",
            gate_type=GateType.ENGINE,
            passed=True,
            score=0.9,
            mode=GateMode.BLOCK,
            message="Pass",
        )
        result = compute_certification(
            gates=[engine_gate],
            validation_passed=True,
            metadata_valid=True,
            has_eval_assets=True,
            operational_policy_result=None,
        )
        op_check = next(c for c in result.trusted.checks if c.check_id == CheckId.OPERATIONAL_POLICY_COMPLIANCE)
        assert op_check.passed is False
        assert "not implemented" in op_check.message
