"""Tests for engine registry and adapters."""

import json

import pytest

from abevalflow.engines import get_all_engines, get_engine
from abevalflow.engines.a2a import A2AEngine
from abevalflow.engines.ase import ASEEngine
from abevalflow.engines.harbor import HarborEngine
from abevalflow.engines.mcpchecker import MCPCheckerEngine
from abevalflow.gates.base import GateType
from abevalflow.schemas import GatePolicy, GatePolicyItem


class TestEngineRegistry:
    """Tests for engine registration and lookup."""

    def test_get_all_engines(self):
        engines = get_all_engines()
        assert "harbor" in engines
        assert "ase" in engines
        assert "a2a" in engines
        assert "mcpchecker" in engines
        assert "aeh" in engines
        assert "aeh_openshell_openclaw" in engines

    def test_get_engine_harbor(self):
        engine = get_engine("harbor")
        assert isinstance(engine, HarborEngine)
        assert engine.name == "harbor"

    def test_get_engine_ase(self):
        engine = get_engine("ase")
        assert isinstance(engine, ASEEngine)
        assert engine.name == "ase"

    def test_get_engine_a2a(self):
        engine = get_engine("a2a")
        assert isinstance(engine, A2AEngine)
        assert engine.name == "a2a"

    def test_get_engine_mcpchecker(self):
        engine = get_engine("mcpchecker")
        assert isinstance(engine, MCPCheckerEngine)
        assert engine.name == "mcpchecker"

    def test_get_engine_aeh(self):
        from abevalflow.engines.aeh import AEHEngine

        engine = get_engine("aeh")
        assert isinstance(engine, AEHEngine)
        assert engine.name == "aeh"

    def test_get_engine_aeh_openshell_openclaw(self):
        from abevalflow.engines.aeh_openshell_openclaw import AEHOpenShellOpenClawEngine

        engine = get_engine("aeh_openshell_openclaw")
        assert isinstance(engine, AEHOpenShellOpenClawEngine)
        assert engine.name == "aeh_openshell_openclaw"

    def test_aeh_openshell_openclaw_stamps_eval_engine(self, tmp_path):
        report = {
            "eval_engine": "aeh",
            "mode": "single",
            "mean_reward": 0.8,
            "judges": {},
            "per_case": {},
        }
        (tmp_path / "report.json").write_text(json.dumps(report))
        engine = get_engine("aeh_openshell_openclaw")
        result = engine.read_result(tmp_path)
        assert result is not None
        assert result["eval_engine"] == "aeh_openshell_openclaw"

    def test_get_engine_unknown(self):
        with pytest.raises(KeyError):
            get_engine("unknown_engine")


class TestHarborEngine:
    """Tests for Harbor engine adapter."""

    def test_read_result_not_found(self, tmp_path):
        engine = HarborEngine()
        result = engine.read_result(tmp_path)
        assert result is None

    def test_read_result_success(self, tmp_path):
        report = {
            "summary": {
                "treatment": {"mean_reward": 0.85, "pass_rate": 0.9},
                "control": {"mean_reward": 0.65, "pass_rate": 0.7},
                "mean_reward_gap": 0.2,
                "recommendation": "pass",
            }
        }
        (tmp_path / "report.json").write_text(json.dumps(report))

        engine = HarborEngine()
        result = engine.read_result(tmp_path)
        assert result is not None
        assert result["summary"]["recommendation"] == "pass"

    def test_to_gate_result_pass(self, tmp_path):
        report = {
            "summary": {
                "treatment": {"mean_reward": 0.85, "pass_rate": 0.9},
                "control": {"mean_reward": 0.65, "pass_rate": 0.7},
                "mean_reward_gap": 0.2,
                "recommendation": "pass",
            }
        }
        policy = GatePolicy()
        engine = HarborEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.gate_type == GateType.ENGINE
        assert gate_result.gate_name == "evaluation"  # category name
        assert gate_result.policy_key == "harbor"  # implementation name
        assert gate_result.details["engine"] == "harbor"
        assert gate_result.passed is True
        assert gate_result.score == 0.85

    def test_to_gate_result_fail(self, tmp_path):
        report = {
            "summary": {
                "treatment": {"mean_reward": 0.3, "pass_rate": 0.4},
                "control": {"mean_reward": 0.65, "pass_rate": 0.7},
                "mean_reward_gap": -0.35,
                "recommendation": "fail",
            }
        }
        policy = GatePolicy()
        engine = HarborEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.passed is False
        assert gate_result.score == 0.3

    def test_default_threshold(self):
        engine = HarborEngine()
        assert engine.get_default_threshold() == 0.0


class TestASEEngine:
    """Tests for ASE engine adapter."""

    def test_to_gate_result(self):
        report = {
            "summary": {
                "treatment": {"mean_reward": 0.95, "pass_rate": 1.0},
                "control": {"mean_reward": 0.75, "pass_rate": 0.8},
                "mean_reward_gap": 0.2,
                "recommendation": "pass",
            }
        }
        policy = GatePolicy()
        engine = ASEEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.gate_type == GateType.ENGINE
        assert gate_result.gate_name == "evaluation"  # category name
        assert gate_result.policy_key == "ase"  # implementation name
        assert gate_result.details["engine"] == "ase"
        assert gate_result.passed is True


class TestA2AEngine:
    """Tests for A2A engine adapter."""

    def test_to_gate_result(self):
        report = {
            "summary": {
                "treatment": {"mean_reward": 0.75, "pass_rate": 0.8},
                "control": {"mean_reward": 0.0},  # A2A has empty control
                "recommendation": "pass",
            }
        }
        policy = GatePolicy()
        engine = A2AEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.gate_type == GateType.ENGINE
        assert gate_result.gate_name == "evaluation"  # category name
        assert gate_result.policy_key == "a2a"  # implementation name
        assert gate_result.details["engine"] == "a2a"
        assert gate_result.passed is True
        assert gate_result.score == 0.75

    def test_default_threshold(self):
        engine = A2AEngine()
        assert engine.get_default_threshold() == 0.5


class TestMCPCheckerEngine:
    """Tests for MCPChecker engine adapter."""

    def test_read_result_mcpchecker_report(self, tmp_path):
        report = {
            "overall_score": 0.8,
            "passed_tasks": 8,
            "failed_tasks": 2,
            "total_tasks": 10,
            "tasks": [],
        }
        (tmp_path / "mcpchecker-report.json").write_text(json.dumps(report))

        engine = MCPCheckerEngine()
        result = engine.read_result(tmp_path)
        assert result is not None
        assert result["overall_score"] == 0.8

    def test_to_gate_result_pass(self):
        report = {
            "overall_score": 0.8,
            "passed_tasks": 8,
            "failed_tasks": 2,
            "error_tasks": 0,
            "total_tasks": 10,
            "tasks": [],
            "recommendation": "pass",
        }
        policy = GatePolicy()
        engine = MCPCheckerEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.gate_type == GateType.ENGINE
        assert gate_result.gate_name == "evaluation"  # category name
        assert gate_result.policy_key == "mcpchecker"  # implementation name
        assert gate_result.details["engine"] == "mcpchecker"
        assert gate_result.passed is True  # 0.8 >= 0.7
        assert gate_result.score == 0.8

    def test_to_gate_result_fail(self):
        report = {
            "overall_score": 0.5,
            "passed_tasks": 5,
            "failed_tasks": 5,
            "error_tasks": 0,
            "total_tasks": 10,
            "tasks": [],
        }
        policy = GatePolicy()
        engine = MCPCheckerEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.passed is False  # 0.5 < 0.7
        assert gate_result.score == 0.5

    def test_to_gate_result_with_failed_tasks(self):
        report = {
            "overall_score": 0.6,
            "passed_tasks": 6,
            "failed_tasks": 3,
            "error_tasks": 1,
            "total_tasks": 10,
            "tasks": [
                {"task_id": "task1", "task_name": "Test 1", "status": "failed", "error_message": "Assertion failed"},
                {"task_id": "task2", "task_name": "Test 2", "status": "error", "error_message": "Timeout"},
            ],
        }
        policy = GatePolicy()
        engine = MCPCheckerEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert len(gate_result.findings) == 2

    def test_default_threshold(self):
        engine = MCPCheckerEngine()
        assert engine.get_default_threshold() == 0.7


class TestThresholdOverridesUpstreamRecommendation:
    """Tests verifying that policy threshold is authoritative, not upstream recommendation."""

    def test_harbor_threshold_overrides_pass_recommendation(self):
        """Upstream says pass but gap is below custom threshold -> gate fails."""
        report = {
            "summary": {
                "treatment": {"mean_reward": 0.7, "pass_rate": 0.8},
                "control": {"mean_reward": 0.6, "pass_rate": 0.7},
                "mean_reward_gap": 0.1,  # Gap is 0.1
                "recommendation": "pass",  # Upstream says pass
            }
        }
        # But policy threshold is 0.2, so 0.1 < 0.2 should fail
        policy = GatePolicy(gates={"evaluation": GatePolicyItem(threshold=0.2)})
        engine = HarborEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.passed is False  # Threshold wins
        assert "upstream: pass" in gate_result.message

    def test_harbor_threshold_overrides_fail_recommendation(self):
        """Upstream says fail but gap meets threshold -> gate passes."""
        report = {
            "summary": {
                "treatment": {"mean_reward": 0.7, "pass_rate": 0.8},
                "control": {"mean_reward": 0.6, "pass_rate": 0.7},
                "mean_reward_gap": 0.1,  # Gap is 0.1
                "recommendation": "fail",  # Upstream says fail (maybe p-value issue)
            }
        }
        # Policy threshold is 0.0 (default), so 0.1 >= 0.0 should pass
        policy = GatePolicy()
        engine = HarborEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.passed is True  # Threshold wins
        assert "upstream: fail" in gate_result.message

    def test_ase_threshold_overrides_upstream(self):
        """ASE engine also uses threshold, not upstream recommendation."""
        report = {
            "summary": {
                "treatment": {"mean_reward": 0.6},
                "control": {"mean_reward": 0.5},
                "mean_reward_gap": 0.1,
                "recommendation": "fail",  # Upstream says fail
            }
        }
        policy = GatePolicy()  # Default threshold is 0.0
        engine = ASEEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.passed is True  # 0.1 >= 0.0
        assert "upstream: fail" in gate_result.message

    def test_a2a_threshold_overrides_upstream(self):
        """A2A engine uses mean_reward against threshold."""
        report = {
            "summary": {
                "treatment": {"mean_reward": 0.6, "pass_rate": 0.7},
                "recommendation": "pass",  # Upstream says pass
            }
        }
        # A2A default threshold is 0.5, but set higher
        policy = GatePolicy(gates={"evaluation": GatePolicyItem(threshold=0.7)})
        engine = A2AEngine()
        gate_result = engine.to_gate_result(report, policy)

        assert gate_result.passed is False  # 0.6 < 0.7
        assert "upstream: pass" in gate_result.message
