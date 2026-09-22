"""Tests for AEH (Agent-Eval-Harness) engine adapter."""

import json

import pytest
import yaml

from abevalflow.engines import get_all_engines, get_engine
from abevalflow.engines.aeh import AEHEngine
from abevalflow.gates.base import GateType
from abevalflow.schemas import GatePolicy, GatePolicyItem


class TestAEHEngineRegistry:
    """Tests for AEH engine registration."""

    def test_aeh_registered(self):
        engines = get_all_engines()
        assert "aeh" in engines

    def test_get_engine_aeh(self):
        engine = get_engine("aeh")
        assert isinstance(engine, AEHEngine)
        assert engine.name == "aeh"


class TestAEHEngine:
    """Tests for AEH engine adapter."""

    def test_read_result_not_found(self, tmp_path):
        engine = AEHEngine()
        result = engine.read_result(tmp_path)
        assert result is None

    def test_read_result_from_report_json(self, tmp_path):
        """Test reading unified report.json format."""
        report = {
            "eval_engine": "aeh",
            "mode": "single",
            "run_id": "test-run-001",
            "mean_reward": 0.85,
            "judges": {"correctness": 0.9, "style": 0.8},
            "per_case": {"case-001": {"reward": 0.85}},
        }
        (tmp_path / "report.json").write_text(json.dumps(report))

        engine = AEHEngine()
        result = engine.read_result(tmp_path)
        assert result is not None
        assert result["mean_reward"] == 0.85
        assert result["mode"] == "single"

    def test_read_result_from_summary_yaml(self, tmp_path):
        """Test reading raw AEH output (summary.yaml)."""
        summary = {
            "run_id": "test-run-001",
            "mean_reward": 0.75,
            "judges": {"correctness": {"mean": 0.8}},
            "per_case": {"case-001": {"reward": 0.75}},
        }
        (tmp_path / "summary.yaml").write_text(yaml.dump(summary))

        engine = AEHEngine()
        result = engine.read_result(tmp_path)
        assert result is not None
        assert result["mean_reward"] == 0.75
        assert result["run_id"] == "test-run-001"

    def test_read_result_from_summary_with_run_result(self, tmp_path):
        """Test reading summary.yaml with run_result.json (overrides mean_reward)."""
        summary = {
            "run_id": "test-run-001",
            "mean_reward": 0.75,
            "judges": {},
            "per_case": {},
        }
        run_result = {
            "mean_reward": 0.85,
            "duration_s": 120.5,
            "cost_usd": 0.15,
        }
        (tmp_path / "summary.yaml").write_text(yaml.dump(summary))
        (tmp_path / "run_result.json").write_text(json.dumps(run_result))

        engine = AEHEngine()
        result = engine.read_result(tmp_path)
        assert result is not None
        assert result["mean_reward"] == 0.85  # From run_result.json
        assert result["execution"]["duration_s"] == 120.5

    def test_read_result_from_nested_run_dir(self, tmp_path):
        """Test reading from reports/<submission>/<run-id>/ structure."""
        run_dir = tmp_path / "test-run-001"
        run_dir.mkdir()
        summary = {
            "run_id": "test-run-001",
            "mean_reward": 0.9,
            "judges": {},
            "per_case": {},
        }
        (run_dir / "summary.yaml").write_text(yaml.dump(summary))

        engine = AEHEngine()
        result = engine.read_result(tmp_path)
        assert result is not None
        assert result["mean_reward"] == 0.9

    def test_to_gate_result_single_pass(self):
        """Test single-run mode with passing result."""
        report = {
            "eval_engine": "aeh",
            "mode": "single",
            "mean_reward": 0.85,
            "judges": {"correctness": 0.9},
            "per_case": {},
        }
        policy = GatePolicy()
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.gate_type == GateType.ENGINE
        assert gate_result.gate_name == "evaluation"
        assert gate_result.policy_key == "aeh"
        assert gate_result.details["engine"] == "aeh"
        assert gate_result.passed is True
        assert gate_result.score == 0.85

    def test_to_gate_result_single_fail(self):
        """Test single-run mode with failing result (below threshold)."""
        report = {
            "eval_engine": "aeh",
            "mode": "single",
            "mean_reward": 0.3,  # Below custom threshold of 0.5
            "judges": {},
            "per_case": {},
        }
        # Set threshold to 0.5 so 0.3 fails
        policy = GatePolicy(gates={"evaluation": GatePolicyItem(threshold=0.5)})
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.passed is False  # 0.3 < 0.5
        assert gate_result.score == 0.3

    def test_to_gate_result_uses_default_threshold(self):
        """Test that default policy uses get_default_threshold() (0.5)."""
        report = {
            "eval_engine": "aeh",
            "mode": "single",
            "mean_reward": 0.3,  # Below default threshold of 0.5
            "judges": {},
            "per_case": {},
        }
        # Default policy with no threshold set
        policy = GatePolicy()
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        # With default threshold of 0.5, mean_reward=0.3 should fail
        assert gate_result.passed is False
        assert gate_result.score == 0.3

    def test_to_gate_result_with_custom_threshold(self):
        """Test that custom threshold is respected."""
        report = {
            "eval_engine": "aeh",
            "mode": "single",
            "mean_reward": 0.6,
            "judges": {},
            "per_case": {},
        }
        # Set threshold to 0.7
        policy = GatePolicy(gates={"evaluation": GatePolicyItem(threshold=0.7)})
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.passed is False  # 0.6 < 0.7
        assert gate_result.threshold == 0.7

    def test_to_gate_result_with_findings(self):
        """Test that low-reward cases are reported as findings."""
        report = {
            "eval_engine": "aeh",
            "mode": "single",
            "mean_reward": 0.5,
            "judges": {},
            "per_case": {
                "case-001": {"reward": 0.9},
                "case-002": {"reward": 0.3},  # Low reward
                "case-003": {"reward": 0.1},  # Very low reward
            },
        }
        policy = GatePolicy()
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert len(gate_result.findings) == 2  # case-002 and case-003

    def test_to_gate_result_pairwise(self):
        """Test pairwise comparison mode."""
        report = {
            "eval_engine": "aeh",
            "mode": "pairwise",
            "mean_reward": 0.0,  # Not used in pairwise
            "pairwise": {
                "run_a": "treatment-001",
                "run_b": "control-001",
                "wins_a": 7,
                "wins_b": 2,
                "ties": 1,
            },
        }
        policy = GatePolicy()
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        # Win rate = 7/10 = 0.7 (ties count in denominator)
        assert gate_result.passed is True
        assert gate_result.score == pytest.approx(0.7)

    def test_default_threshold(self):
        engine = AEHEngine()
        assert engine.get_default_threshold() == 0.5

    def test_message_includes_judge_summary(self):
        """Test that message includes judge scores."""
        report = {
            "eval_engine": "aeh",
            "mode": "single",
            "mean_reward": 0.85,
            "judges": {"correctness": 0.9, "style": 0.8},
            "per_case": {},
        }
        policy = GatePolicy()
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert "correctness=0.90" in gate_result.message
        assert "style=0.80" in gate_result.message

    def test_pairwise_with_threshold(self):
        """Test pairwise mode with custom threshold."""
        report = {
            "eval_engine": "aeh",
            "mode": "pairwise",
            "pairwise": {
                "wins_a": 3,
                "wins_b": 4,
                "ties": 3,
            },
        }
        # Win rate = 3/10 = 0.3, fails default threshold of 0.5
        policy = GatePolicy()
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.passed is False
        assert gate_result.score == pytest.approx(0.3)
        assert gate_result.threshold == 0.5

    def test_pairwise_all_ties_passes(self):
        """All-ties: score is 0%, but gate still passes."""
        report = {
            "eval_engine": "aeh",
            "mode": "pairwise",
            "pairwise": {
                "wins_a": 0,
                "wins_b": 0,
                "ties": 1,
            },
        }
        policy = GatePolicy()
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.passed is True
        assert gate_result.score == 0.0

    def test_single_none_mean_reward_score_is_zero(self):
        """Missing mean_reward must not crash GateResult; score floors at 0.0."""
        report = {
            "eval_engine": "aeh",
            "mode": "single",
            "mean_reward": None,
            "summary": {},
            "judges": {},
            "per_case": {},
        }
        policy = GatePolicy()
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.passed is False
        assert gate_result.score == 0.0

    def test_single_judge_errors_are_not_quality_zero(self):
        report = {
            "eval_engine": "aeh_openshell_openclaw",
            "mode": "single",
            "mean_reward": None,
            "summary": {
                "treatment": {"mean_reward": None, "n_errors": 1},
                "control": {"mean_reward": None},
            },
            "judges": {"analysis_accuracy": {"mean": None, "errored_cases": 1}},
            "per_case": {
                "analysis-panel": {
                    "analysis_accuracy": {
                        "value": None,
                        "error": "No module named 'anthropic'",
                        "judge_type": "llm",
                    }
                }
            },
        }
        policy = GatePolicy()
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.passed is False
        assert gate_result.details["scoring_unavailable"] is True
        assert gate_result.details["comparison"] == "single"
        assert "not a quality score of 0.0" in gate_result.message
        assert any(f.rule_id == "aeh-judge-error" for f in gate_result.findings)

    def test_pairwise_errors_in_denominator(self):
        """Errors count as non-wins so 1/10 with 9 errors fails the gate."""
        report = {
            "eval_engine": "aeh",
            "mode": "pairwise",
            "pairwise": {
                "wins_a": 1,
                "wins_b": 0,
                "ties": 0,
                "errors": 9,
            },
        }
        policy = GatePolicy()
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.score == pytest.approx(0.1)
        assert gate_result.passed is False

    def test_pairwise_with_stability(self):
        """Test pairwise mode with stability metrics."""
        report = {
            "eval_engine": "aeh",
            "mode": "pairwise",
            "pairwise": {
                "wins_a": 7,
                "wins_b": 2,
                "ties": 1,
                "stability": {
                    "runs": 3,
                    "agreement_rate": 0.9,
                    "stable_cases": 9,
                    "total_cases": 10,
                },
            },
        }
        policy = GatePolicy()
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert "stability=90%" in gate_result.message
        assert gate_result.details["pairwise"]["stability"]["agreement_rate"] == 0.9

    def test_pairwise_findings_extraction(self):
        """Test that pairwise findings include control wins and errors."""
        report = {
            "eval_engine": "aeh",
            "mode": "pairwise",
            "pairwise": {
                "wins_a": 5,
                "wins_b": 3,
                "ties": 2,
                "per_case": [
                    {"case_id": "case-001", "winner": "A", "error": None},
                    {"case_id": "case-002", "winner": "B", "error": None},
                    {"case_id": "case-003", "winner": "error", "error": "API timeout"},
                    {"case_id": "case-004", "winner": "tie", "error": None},
                ],
            },
        }
        policy = GatePolicy()
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        # Should have finding for case-002 (control wins) and case-003 (error)
        assert len(gate_result.findings) == 2
        finding_messages = [f.message for f in gate_result.findings]
        assert any("case-002" in m and "control beat treatment" in m for m in finding_messages)
        assert any("case-003" in m and "error" in m for m in finding_messages)

    def test_judge_passthrough_dict_format(self):
        """Test that full judge metadata (dict format) is preserved."""
        report = {
            "eval_engine": "aeh",
            "mode": "single",
            "mean_reward": 0.8,
            "judges": {
                "exit_success": {
                    "mean": 1.0,
                    "pass_rate": 1.0,
                },
                "output_quality": {
                    "mean": 3.8,
                    "pass_rate": None,
                    "stability": {
                        "samples": 3,
                        "stable_cases": 8,
                        "total_cases": 10,
                    },
                },
            },
            "per_case": {},
        }
        policy = GatePolicy()
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        # Check full metadata is preserved in details
        judges = gate_result.details["judges"]
        assert judges["exit_success"]["pass_rate"] == 1.0
        assert judges["output_quality"]["stability"]["samples"] == 3

        # Check message formats pass_rate as percentage
        assert "exit_success=100%" in gate_result.message

    def test_per_case_findings_with_judge_types(self):
        """Test findings extraction from per_case with AEH's nested judge structure."""
        report = {
            "eval_engine": "aeh",
            "mode": "single",
            "mean_reward": 0.5,
            "judges": {},
            "per_case": {
                "case-001": {
                    "exit_success": {
                        "value": True,
                        "rationale": "Success",
                        "judge_type": "check",
                    },
                },
                "case-002": {
                    "exit_success": {
                        "value": False,
                        "rationale": "Failed",
                        "judge_type": "check",
                    },
                },
                "case-003": {
                    "output_quality": {
                        "value": 2,
                        "rationale": "Low quality",
                        "judge_type": "llm",
                    },
                },
                "case-004": {
                    "syntax_check": {
                        "value": None,
                        "error": "Parser failed",
                        "judge_type": "check",
                    },
                },
            },
        }
        policy = GatePolicy()
        engine = AEHEngine()
        gate_result = engine.to_gate_result(report, policy)

        # Should have findings for: case-002 (failed), case-003 (low score), case-004 (error)
        assert len(gate_result.findings) == 3
        finding_rules = [f.rule_id for f in gate_result.findings]
        assert "aeh-judge-failed" in finding_rules
        assert "aeh-low-score" in finding_rules
        assert "aeh-judge-error" in finding_rules

    def test_read_result_detects_pairwise_mode(self, tmp_path):
        """Test that mode is correctly detected from summary.yaml pairwise key."""
        summary = {
            "run_id": "treatment-001",
            "mean_reward": 0.8,
            "judges": {},
            "per_case": {},
            "pairwise": {
                "run_a": "treatment-001",
                "run_b": "control-001",
                "wins_a": 5,
                "wins_b": 3,
                "ties": 2,
            },
        }
        (tmp_path / "summary.yaml").write_text(yaml.dump(summary))

        engine = AEHEngine()
        result = engine.read_result(tmp_path)

        assert result is not None
        assert result["mode"] == "pairwise"
        assert result["pairwise"]["wins_a"] == 5
