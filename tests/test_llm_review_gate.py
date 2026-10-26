"""Submission review verdicts and blocking policy thresholds."""

import json

from abevalflow.gates.base import GateMode, GateType
from abevalflow.gates.quality.llm_review import LLMReviewGate
from abevalflow.schemas import GatePolicy, GatePolicyItem


class TestLLMReviewGate:
    """Tests for LLM review quality gate."""

    def test_evaluate_disabled(self, tmp_path):
        policy = GatePolicy(gates={"quality": GatePolicyItem(mode=GateMode.DISABLED)})
        gate = LLMReviewGate()
        result = gate.evaluate(tmp_path, policy)

        assert result.gate_type == GateType.QUALITY
        assert result.gate_name == "quality"
        assert result.policy_key == "llm-review"
        assert result.details["reviewer"] == "llm-review"
        assert result.passed is True
        assert result.mode == GateMode.DISABLED

    def test_evaluate_no_review_file(self, tmp_path):
        policy = GatePolicy()
        gate = LLMReviewGate()
        result = gate.evaluate(tmp_path, policy)

        assert result.passed is True
        assert "not_found" in result.details.get("status", "")

    def test_evaluate_pass_recommendation(self, tmp_path):
        review_data = {
            "dimensions": {
                "coherence": {"score": 0.8, "finding": "Good coherence"},
                "coverage": {"score": 0.7, "finding": "Adequate coverage"},
                "clarity": {"score": 0.9, "finding": "Very clear"},
                "feasibility": {"score": 0.75, "finding": "Feasible"},
                "robustness": {"score": 0.65, "finding": "OK robustness"},
            },
            "overall_score": 0.76,
            "recommendation": "pass",
            "summary": "Overall good submission",
        }
        (tmp_path / "_ai_review.json").write_text(json.dumps(review_data))

        policy = GatePolicy()
        gate = LLMReviewGate()
        result = gate.evaluate(tmp_path, policy)

        assert result.passed is True
        assert result.score == 0.76
        assert result.details["recommendation"] == "pass"

    def test_evaluate_fail_recommendation(self, tmp_path):
        review_data = {
            "dimensions": {
                "coherence": {"score": 0.3, "finding": "Poor coherence"},
                "coverage": {"score": 0.4, "finding": "Low coverage"},
                "clarity": {"score": 0.5, "finding": "Unclear"},
                "feasibility": {"score": 0.3, "finding": "Not feasible"},
                "robustness": {"score": 0.2, "finding": "Very weak"},
            },
            "overall_score": 0.34,
            "recommendation": "fail",
            "summary": "Major issues",
        }
        (tmp_path / "_ai_review.json").write_text(json.dumps(review_data))

        policy = GatePolicy()
        gate = LLMReviewGate()
        result = gate.evaluate(tmp_path, policy)

        assert result.passed is False
        assert len(result.findings) > 0  # Should have findings for low scores

    def test_evaluate_warn_recommendation_block_mode(self, tmp_path):
        review_data = {
            "dimensions": {
                "coherence": {"score": 0.55, "finding": "OK coherence"},
                "coverage": {"score": 0.5, "finding": "Marginal coverage"},
                "clarity": {"score": 0.6, "finding": "Clear enough"},
                "feasibility": {"score": 0.55, "finding": "Probably feasible"},
                "robustness": {"score": 0.5, "finding": "Weak"},
            },
            "overall_score": 0.54,
            "recommendation": "warn",
            "summary": "Marginal submission",
        }
        (tmp_path / "_ai_review.json").write_text(json.dumps(review_data))

        policy = GatePolicy(gates={"quality": GatePolicyItem(mode=GateMode.BLOCK, threshold=0.6)})
        gate = LLMReviewGate()
        result = gate.evaluate(tmp_path, policy)

        # In block mode with threshold 0.6, score 0.54 should fail
        assert result.passed is False
        assert result.threshold == 0.6

    def test_evaluate_warn_recommendation_warn_mode(self, tmp_path):
        review_data = {
            "dimensions": {
                "coherence": {"score": 0.55, "finding": "OK"},
            },
            "overall_score": 0.55,
            "recommendation": "warn",
            "summary": "Marginal",
        }
        (tmp_path / "_ai_review.json").write_text(json.dumps(review_data))

        policy = GatePolicy(gates={"quality": GatePolicyItem(mode=GateMode.WARN)})
        gate = LLMReviewGate()
        result = gate.evaluate(tmp_path, policy)

        # In warn mode, warn recommendation passes
        assert result.passed is True

    def test_block_mode_fail_recommendation_high_score(self, tmp_path):
        """In BLOCK mode, score threshold is authoritative - fail recommendation can be overridden."""
        review_data = {
            "dimensions": {
                "coherence": {"score": 0.7, "finding": "Good"},
                "coverage": {"score": 0.65, "finding": "Acceptable"},
            },
            "overall_score": 0.65,  # Above default threshold of 0.6
            "recommendation": "fail",  # LLM says fail
            "summary": "Some concerns",
        }
        (tmp_path / "_ai_review.json").write_text(json.dumps(review_data))

        policy = GatePolicy(gates={"quality": GatePolicyItem(mode=GateMode.BLOCK, threshold=0.6)})
        gate = LLMReviewGate()
        result = gate.evaluate(tmp_path, policy)

        # In BLOCK mode, 0.65 >= 0.6 threshold means PASS despite fail recommendation
        assert result.passed is True
        assert "upstream: fail" in result.message

    def test_block_mode_pass_recommendation_low_score(self, tmp_path):
        """In BLOCK mode, score below threshold fails even if recommendation is pass."""
        review_data = {
            "dimensions": {
                "coherence": {"score": 0.5, "finding": "OK"},
            },
            "overall_score": 0.5,  # Below threshold
            "recommendation": "pass",  # LLM says pass
            "summary": "Looks fine",
        }
        (tmp_path / "_ai_review.json").write_text(json.dumps(review_data))

        policy = GatePolicy(gates={"quality": GatePolicyItem(mode=GateMode.BLOCK, threshold=0.6)})
        gate = LLMReviewGate()
        result = gate.evaluate(tmp_path, policy)

        # In BLOCK mode, 0.5 < 0.6 threshold means FAIL despite pass recommendation
        assert result.passed is False
        assert "upstream: pass" in result.message

    def test_block_mode_missing_artifact_fails(self, tmp_path):
        """In BLOCK mode, missing _ai_review.json fails the gate."""
        policy = GatePolicy(gates={"quality": GatePolicyItem(mode=GateMode.BLOCK)})
        gate = LLMReviewGate()
        result = gate.evaluate(tmp_path, policy)

        assert result.passed is False
        assert "required in block mode" in result.message
