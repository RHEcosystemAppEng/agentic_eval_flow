"""Tests for unified scorecard schemas and aggregation logic."""

import json
from datetime import UTC, datetime

import pytest

from abevalflow.gates.base import Finding, GateMode, GateResult, GateType, Severity
from abevalflow.schemas import CombinationMode, GatePolicy, GatePolicyItem
from abevalflow.scorecard import Recommendation, Scorecard, apply_combination_logic


class TestGateResult:
    """Tests for GateResult schema."""

    def test_create_engine_gate(self):
        gate = GateResult(
            gate_type=GateType.ENGINE,
            gate_name="evaluation",
            policy_key="harbor",
            passed=True,
            score=0.85,
            mode=GateMode.BLOCK,
            message="Test passed",
            details={"engine": "harbor"},
        )
        assert gate.gate_type == GateType.ENGINE
        assert gate.gate_name == "evaluation"
        assert gate.policy_key == "harbor"
        assert gate.get_policy_key() == "harbor"
        assert gate.passed is True
        assert gate.score == 0.85
        assert gate.mode == GateMode.BLOCK

    def test_create_security_gate_with_findings(self):
        findings = [
            Finding(
                severity=Severity.HIGH,
                message="SQL injection vulnerability",
                location="src/db.py",
                rule_id="SEC001",
            ),
            Finding(
                severity=Severity.MEDIUM,
                message="Unused import",
                location="src/utils.py",
            ),
        ]
        gate = GateResult(
            gate_type=GateType.SECURITY,
            gate_name="security",
            policy_key="cisco",
            passed=False,
            score=0.3,
            mode=GateMode.BLOCK,
            findings=findings,
            details={"scanner": "cisco"},
        )
        assert gate.gate_type == GateType.SECURITY
        assert gate.gate_name == "security"
        assert gate.policy_key == "cisco"
        assert len(gate.findings) == 2
        assert gate.findings[0].severity == Severity.HIGH

    def test_score_validation(self):
        with pytest.raises(ValueError):
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="test",
                passed=True,
                score=1.5,  # Invalid: > 1.0
                mode=GateMode.WARN,
            )


class TestGatePolicy:
    """Tests for GatePolicy schema."""

    def test_default_policy(self):
        policy = GatePolicy()
        assert policy.default_mode == GateMode.WARN
        assert policy.combination == CombinationMode.ALL_PASS
        assert len(policy.gates) == 0

    def test_get_gate_policy_default(self):
        policy = GatePolicy(default_mode=GateMode.BLOCK)
        item = policy.get_gate_policy("unknown_gate")
        assert item.mode == GateMode.BLOCK

    def test_get_gate_policy_override(self):
        policy = GatePolicy(
            default_mode=GateMode.WARN,
            gates={
                "harbor": GatePolicyItem(mode=GateMode.BLOCK, threshold=0.5),
            },
        )
        harbor_policy = policy.get_gate_policy("harbor")
        assert harbor_policy.mode == GateMode.BLOCK
        assert harbor_policy.threshold == 0.5

        other_policy = policy.get_gate_policy("other")
        assert other_policy.mode == GateMode.WARN

    def test_is_enabled(self):
        policy = GatePolicy(
            gates={
                "cisco": GatePolicyItem(mode=GateMode.DISABLED),
                "harbor": GatePolicyItem(mode=GateMode.BLOCK),
            },
        )
        assert policy.is_enabled("harbor") is True
        assert policy.is_enabled("cisco") is False
        assert policy.is_enabled("unknown") is True  # Default is warn


class TestCombinationLogic:
    """Tests for gate combination logic."""

    def test_all_pass_success(self):
        gates = [
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="harbor",
                passed=True,
                score=0.9,
                mode=GateMode.BLOCK,
            ),
            GateResult(
                gate_type=GateType.SECURITY,
                gate_name="security",
                policy_key="cisco",
                passed=True,
                score=1.0,
                mode=GateMode.BLOCK,
            ),
        ]
        policy = GatePolicy(combination=CombinationMode.ALL_PASS)
        rec, reason = apply_combination_logic(gates, policy)
        assert rec == Recommendation.PASS
        assert "All gates passed" in reason

    def test_all_pass_failure(self):
        gates = [
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="harbor",
                passed=True,
                score=0.9,
                mode=GateMode.BLOCK,
            ),
            GateResult(
                gate_type=GateType.SECURITY,
                gate_name="security",
                policy_key="cisco",
                passed=False,
                score=0.3,
                mode=GateMode.BLOCK,
            ),
        ]
        policy = GatePolicy(combination=CombinationMode.ALL_PASS)
        rec, reason = apply_combination_logic(gates, policy)
        assert rec == Recommendation.FAIL
        assert "security" in reason  # Now uses category name in reason

    def test_all_pass_warn_only(self):
        gates = [
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="harbor",
                passed=True,
                score=0.9,
                mode=GateMode.BLOCK,
            ),
            GateResult(
                gate_type=GateType.QUALITY,
                gate_name="quality",
                policy_key="llm-review",
                passed=False,
                score=0.4,
                mode=GateMode.WARN,
            ),
        ]
        policy = GatePolicy(combination=CombinationMode.ALL_PASS)
        rec, reason = apply_combination_logic(gates, policy)
        assert rec == Recommendation.WARN
        assert "quality" in reason  # Now uses category name in reason

    def test_any_pass_success(self):
        gates = [
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="harbor",
                passed=True,
                score=0.9,
                mode=GateMode.BLOCK,
            ),
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="ase",
                passed=False,
                score=0.3,
                mode=GateMode.BLOCK,
            ),
        ]
        policy = GatePolicy(combination=CombinationMode.ANY_PASS)
        rec, reason = apply_combination_logic(gates, policy)
        assert rec == Recommendation.PASS

    def test_any_pass_failure(self):
        gates = [
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="harbor",
                passed=False,
                score=0.3,
                mode=GateMode.BLOCK,
            ),
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="ase",
                passed=False,
                score=0.2,
                mode=GateMode.BLOCK,
            ),
        ]
        policy = GatePolicy(combination=CombinationMode.ANY_PASS)
        rec, reason = apply_combination_logic(gates, policy)
        assert rec == Recommendation.FAIL

    def test_weighted_pass(self):
        gates = [
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="harbor",
                passed=True,
                score=0.9,
                mode=GateMode.BLOCK,
            ),
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="ase",
                passed=True,
                score=0.6,
                mode=GateMode.BLOCK,
            ),
        ]
        policy = GatePolicy(
            combination=CombinationMode.WEIGHTED,
            gates={
                "harbor": GatePolicyItem(weight=2.0),
                "ase": GatePolicyItem(weight=1.0),
            },
        )
        rec, reason = apply_combination_logic(gates, policy)
        # Weighted: (0.9*2 + 0.6*1) / 3 = 0.8 >= 0.7
        assert rec == Recommendation.PASS

    def test_weighted_warn(self):
        gates = [
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="harbor",
                passed=True,
                score=0.6,
                mode=GateMode.BLOCK,
            ),
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="ase",
                passed=True,
                score=0.5,
                mode=GateMode.BLOCK,
            ),
        ]
        policy = GatePolicy(combination=CombinationMode.WEIGHTED)
        rec, reason = apply_combination_logic(gates, policy)
        # (0.6 + 0.5) / 2 = 0.55 - between 0.5 and 0.7
        assert rec == Recommendation.WARN

    def test_empty_gates(self):
        policy = GatePolicy()
        rec, reason = apply_combination_logic([], policy)
        assert rec == Recommendation.FAIL
        assert "No gates" in reason


class TestScorecard:
    """Tests for Scorecard schema."""

    def test_create_scorecard(self):
        gates = [
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="harbor",
                passed=True,
                score=0.85,
                mode=GateMode.BLOCK,
            ),
            GateResult(
                gate_type=GateType.SECURITY,
                gate_name="security",
                policy_key="cisco",
                passed=True,
                score=1.0,
                mode=GateMode.WARN,
            ),
        ]
        policy = GatePolicy()
        scorecard = Scorecard(
            submission_name="test-submission",
            pipeline_run_id="run-123",
            eval_engine="aeh",
            gates=gates,
            policy=policy,
            recommendation=Recommendation.PASS,
            recommendation_reason="All gates passed",
        )
        assert scorecard.submission_name == "test-submission"
        assert scorecard.gates_passed == 2
        assert scorecard.gates_failed == 0
        assert scorecard.blocking_gates_passed == 1
        assert scorecard.blocking_gates_failed == 0

    def test_computed_fields(self):
        gates = [
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="harbor",
                passed=True,
                score=0.85,
                mode=GateMode.BLOCK,
            ),
            GateResult(
                gate_type=GateType.SECURITY,
                gate_name="security",
                policy_key="cisco",
                passed=False,
                score=0.3,
                mode=GateMode.BLOCK,
            ),
            GateResult(
                gate_type=GateType.QUALITY,
                gate_name="quality",
                policy_key="llm-review",
                passed=False,
                score=0.4,
                mode=GateMode.WARN,
            ),
        ]
        scorecard = Scorecard(
            submission_name="test",
            pipeline_run_id="run-456",
            eval_engine="aeh",
            gates=gates,
            policy=GatePolicy(),
            recommendation=Recommendation.FAIL,
            recommendation_reason="Blocking gates failed",
        )
        assert scorecard.gates_passed == 1
        assert scorecard.gates_failed == 2
        assert scorecard.blocking_gates_passed == 1
        assert scorecard.blocking_gates_failed == 1

    def test_scorecard_json_serialization(self):
        gates = [
            GateResult(
                gate_type=GateType.ENGINE,
                gate_name="evaluation",
                policy_key="harbor",
                passed=True,
                score=0.85,
                mode=GateMode.BLOCK,
            ),
        ]
        scorecard = Scorecard(
            submission_name="test",
            pipeline_run_id="run-789",
            eval_engine="aeh",
            gates=gates,
            policy=GatePolicy(),
            recommendation=Recommendation.PASS,
            recommendation_reason="OK",
            created_at=datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC),
        )
        json_str = scorecard.model_dump_json()
        data = json.loads(json_str)
        assert data["submission_name"] == "test"
        assert data["recommendation"] == "pass"
        assert data["gates_passed"] == 1
        assert len(data["gates"]) == 1
