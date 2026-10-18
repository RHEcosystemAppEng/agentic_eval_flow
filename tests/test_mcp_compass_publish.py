"""Tests for MCP Compass publish (write side).

Covers:
- Domain model (eval_result.py): summary loaders, phase independence, computed fields
- Compass publish (compass_publish.py): payload shapes, consumed-check exclusion,
  dry-run mode, push orchestration
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from abevalflow.mcp.checks.compass_publish import (
    ALL_FACT_MAPS,
    MCP_EVAL_SUMMARY_FACT,
    PHASE1_FACT_MAP,
    PHASE_SUMMARY_FACTS,
    McpPushConfig,
    _build_check_fact_payload,
    _build_mcp_summary_payload,
    _build_phase_summary_payload,
    push_mcp_eval_facts,
)
from abevalflow.mcp.eval_result import (
    CONSUMED_CHECKS,
    McpCheckResult,
    McpEvalResult,
    McpPhase,
    McpPhaseResult,
    build_mcp_eval_result,
    load_phase1_summary,
    load_phase2_summary,
)
from scripts.mcp.bootstrap_compass_track import (
    ALL_CHECK_DEFS,
    CONSUMED_TRACK_CHECKS,
    OWNER,
    TRACK_DEFINITION,
    TRACK_ID,
    _register_checks,
    _register_track,
)
from scripts.mcp.runners.compass_publish import _summary_path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PHASE1_SUMMARY_ALL_PASS = {
    "phase": "phase1",
    "passed": True,
    "gates": [
        {"gate": "mcp-secrets", "passed": True, "score": 1.0, "findings": 0, "message": "No secrets found"},
        {"gate": "mcp-no-user-code", "passed": True, "score": 1.0, "findings": 0, "message": "No user-code execution"},
        {"gate": "mcp-license", "passed": True, "score": 1.0, "findings": 0, "message": "License compliant"},
    ],
}

PHASE1_SUMMARY_ONE_FAIL = {
    "phase": "phase1",
    "passed": False,
    "gates": [
        {"gate": "mcp-secrets", "passed": False, "score": 0.25, "findings": 2, "message": "2 secrets found"},
        {"gate": "mcp-no-user-code", "passed": True, "score": 1.0, "findings": 0, "message": "Clean"},
        {"gate": "mcp-license", "passed": True, "score": 1.0, "findings": 0, "message": "License OK"},
    ],
}

PHASE2_SUMMARY_MIXED = {
    "phase": "phase2",
    "passed": False,
    "counts": {"pass": 6, "fail": 1, "not_evaluated": 3},
    "checks": [
        {
            "check": "http-support",
            "status": "pass",
            "passed": True,
            "score": 1.0,
            "findings": 0,
            "source": "probe",
            "message": "HTTP OK",
        },
        {
            "check": "protocol-compliance",
            "status": "pass",
            "passed": True,
            "score": 1.0,
            "findings": 0,
            "source": "probe",
            "message": "Protocol OK",
        },
        {
            "check": "schema-conformance",
            "status": "pass",
            "passed": True,
            "score": 1.0,
            "findings": 0,
            "source": "probe",
            "message": "Schema OK",
        },
        {
            "check": "tool-annotations",
            "status": "pass",
            "passed": True,
            "score": 1.0,
            "findings": 0,
            "source": "probe",
            "message": "Annotations OK",
        },
        {
            "check": "rate-limiting",
            "status": "not_evaluated",
            "passed": True,
            "score": 1.0,
            "findings": 0,
            "source": "probe",
            "message": "No 429",
        },
        {
            "check": "response-size-limit",
            "status": "not_evaluated",
            "passed": True,
            "score": 1.0,
            "findings": 0,
            "source": "probe",
            "message": "Deferred",
        },
        {
            "check": "mandatory-timeouts",
            "status": "not_evaluated",
            "passed": True,
            "score": 1.0,
            "findings": 0,
            "source": "probe",
            "message": "Deferred",
        },
        {
            "check": "openapi-conformance",
            "status": "pass",
            "passed": True,
            "score": 1.0,
            "findings": 0,
            "source": "probe",
            "message": "OpenAPI OK",
        },
        {
            "check": "tool-name-rules",
            "status": "pass",
            "passed": True,
            "score": 1.0,
            "findings": 0,
            "source": "compass",
            "message": "Tool names valid",
        },
        {
            "check": "oauth-catalog-match",
            "status": "fail",
            "passed": True,
            "score": 0.0,
            "findings": 1,
            "source": "compass",
            "message": "OAuth mismatch",
        },
    ],
}


def _write_summary(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data, indent=2))
    return path


# ---------------------------------------------------------------------------
# eval_result.py — Model tests
# ---------------------------------------------------------------------------


class TestMcpCheckResult:
    def test_defaults(self):
        c = McpCheckResult(check_id="mcp-secrets", name="Secrets", passed=True, score=1.0, status="pass")
        assert c.source == "pipeline"
        assert c.publishable is True

    def test_consumed_flag(self):
        c = McpCheckResult(
            check_id="tool-name-rules",
            name="Tool Names",
            passed=True,
            score=1.0,
            status="pass",
            publishable=False,
        )
        assert c.publishable is False


class TestMcpPhaseResult:
    def test_computed_fields(self):
        checks = [
            McpCheckResult(check_id="a", name="A", passed=True, score=1.0, status="pass"),
            McpCheckResult(check_id="b", name="B", passed=False, score=0.0, status="fail"),
            McpCheckResult(check_id="c", name="C", passed=True, score=0.5, status="pass"),
        ]
        phase = McpPhaseResult(phase=McpPhase.PHASE1, passed=False, checks=checks)
        assert phase.checks_passed == 2
        assert phase.checks_total == 3
        assert phase.overall_score == pytest.approx(0.5)

    def test_empty_checks(self):
        phase = McpPhaseResult(phase=McpPhase.PHASE1, passed=True, checks=[])
        assert phase.overall_score == 0.0
        assert phase.checks_passed == 0


class TestMcpEvalResult:
    def test_phases_passed_without_phase3(self):
        p1 = McpPhaseResult(phase=McpPhase.PHASE1, passed=True, checks=[])
        p2 = McpPhaseResult(phase=McpPhase.PHASE2, passed=False, checks=[])
        result = McpEvalResult(phase1=p1, phase2=p2)
        assert result.phases_passed == {"phase1": True, "phase2": False}
        assert result.phase3 is None

    def test_phases_passed_with_phase3(self):
        p1 = McpPhaseResult(phase=McpPhase.PHASE1, passed=True, checks=[])
        p2 = McpPhaseResult(phase=McpPhase.PHASE2, passed=True, checks=[])
        p3 = McpPhaseResult(phase=McpPhase.PHASE3, passed=False, checks=[])
        result = McpEvalResult(phase1=p1, phase2=p2, phase3=p3)
        assert result.phases_passed == {"phase1": True, "phase2": True, "phase3": False}


# ---------------------------------------------------------------------------
# eval_result.py — Loader tests
# ---------------------------------------------------------------------------


class TestLoadPhase1Summary:
    def test_all_pass(self, tmp_path):
        path = _write_summary(tmp_path / "phase1-summary.json", PHASE1_SUMMARY_ALL_PASS)
        result = load_phase1_summary(path)
        assert result.phase == McpPhase.PHASE1
        assert result.passed is True
        assert result.checks_total == 3
        assert all(c.publishable for c in result.checks)
        assert all(c.source == "pipeline" for c in result.checks)

    def test_one_fail(self, tmp_path):
        path = _write_summary(tmp_path / "phase1-summary.json", PHASE1_SUMMARY_ONE_FAIL)
        result = load_phase1_summary(path)
        assert result.passed is False
        secrets = next(c for c in result.checks if c.check_id == "mcp-secrets")
        assert secrets.passed is False
        assert secrets.score == 0.25
        assert secrets.status == "fail"

    def test_human_names(self, tmp_path):
        path = _write_summary(tmp_path / "phase1-summary.json", PHASE1_SUMMARY_ALL_PASS)
        result = load_phase1_summary(path)
        names = {c.check_id: c.name for c in result.checks}
        assert names["mcp-secrets"] == "Secrets Management"
        assert names["mcp-no-user-code"] == "No User-Code Execution"
        assert names["mcp-license"] == "License Compliance"


class TestLoadPhase2Summary:
    def test_consumed_checks_not_publishable(self, tmp_path):
        path = _write_summary(tmp_path / "phase2-summary.json", PHASE2_SUMMARY_MIXED)
        result = load_phase2_summary(path)
        for check in result.checks:
            if check.check_id in CONSUMED_CHECKS:
                assert check.publishable is False, f"{check.check_id} should not be publishable"
            else:
                assert check.publishable is True, f"{check.check_id} should be publishable"

    def test_source_preserved(self, tmp_path):
        path = _write_summary(tmp_path / "phase2-summary.json", PHASE2_SUMMARY_MIXED)
        result = load_phase2_summary(path)
        tool_name = next(c for c in result.checks if c.check_id == "tool-name-rules")
        assert tool_name.source == "compass"
        http = next(c for c in result.checks if c.check_id == "http-support")
        assert http.source == "probe"

    def test_three_state_status(self, tmp_path):
        path = _write_summary(tmp_path / "phase2-summary.json", PHASE2_SUMMARY_MIXED)
        result = load_phase2_summary(path)
        rate = next(c for c in result.checks if c.check_id == "rate-limiting")
        assert rate.status == "not_evaluated"
        assert rate.passed is None
        assert rate.score is None
        oauth = next(c for c in result.checks if c.check_id == "oauth-catalog-match")
        assert oauth.status == "fail"
        assert oauth.passed is False

    def test_not_evaluated_checks_do_not_inflate_phase_score(self, tmp_path):
        path = _write_summary(tmp_path / "phase2-summary.json", PHASE2_SUMMARY_MIXED)
        result = load_phase2_summary(path)
        assert result.overall_score == pytest.approx(6 / 7)
        assert result.checks_evaluated == 7


class TestBuildMcpEvalResult:
    def test_no_hierarchy(self, tmp_path):
        """Phase 1 fail does NOT cascade to Phase 2.

        Phase 2 reports its own pass/fail based on its checks (here: False
        because oauth-catalog-match failed), independent of Phase 1.
        """
        p1_path = _write_summary(tmp_path / "phase1-summary.json", PHASE1_SUMMARY_ONE_FAIL)
        p2_path = _write_summary(tmp_path / "phase2-summary.json", PHASE2_SUMMARY_MIXED)
        p1 = load_phase1_summary(p1_path)
        p2 = load_phase2_summary(p2_path)
        result = build_mcp_eval_result(p1, p2)
        assert result.phase1.passed is False
        assert result.phase2.passed is False
        assert result.phase2.checks_passed == 6
        assert result.phase2.checks_total == 10


class TestSummaryPath:
    def test_finds_nested_phase_summary(self, tmp_path):
        phase_dir = tmp_path / "phase1"
        phase_dir.mkdir()
        expected = phase_dir / "phase1-summary.json"
        expected.write_text("{}")
        assert _summary_path(tmp_path, "phase1", "phase1-summary.json") == expected

    def test_supports_flat_summary_layout(self, tmp_path):
        expected = tmp_path / "phase1-summary.json"
        expected.write_text("{}")
        assert _summary_path(tmp_path, "phase1", "phase1-summary.json") == expected


class TestCompassTrackBootstrap:
    def test_track_is_separate_and_reuses_existing_compass_checks(self):
        assert TRACK_ID == "abevalflow-mcp-evaluation"
        assert len(TRACK_DEFINITION["levels"]) == 2
        phase1, phase2 = TRACK_DEFINITION["levels"]
        assert (phase1["ordinal"], phase1["name"]) == (1, "Phase 1 — Static Analysis")
        assert (phase2["ordinal"], phase2["name"]) == (2, "Phase 2 — Conformance Testing")
        phase1_ids = {check["id"] for check in phase1["checks"]}
        phase2_ids = {check["id"] for check in phase2["checks"]}
        assert len(phase1_ids) == 3
        assert len(phase2_ids) == 10
        assert phase1_ids.isdisjoint(phase2_ids)
        assert "mcp-tool-schema-check" in phase2_ids
        assert "mcp-oauth-scope-match" in phase2_ids
        assert not any("summary" in check_id for check_id in phase1_ids | phase2_ids)
        assert "mcp-eval-tool-name-rules" not in phase2_ids
        assert "mcp-eval-oauth-catalog-match" not in phase2_ids
        assert "mcp-eval-phase3-summary" not in phase1_ids | phase2_ids
        registered_ids = {check["id"] for check in ALL_CHECK_DEFS}
        assert registered_ids.isdisjoint({check["id"] for check in CONSUMED_TRACK_CHECKS})
        assert len(ALL_CHECK_DEFS) == 11

    def test_track_dry_run_does_not_make_http_request(self):
        with patch("scripts.mcp.bootstrap_compass_track.urllib.request.urlopen") as mock_urlopen:
            assert _register_track("https://compass.example.com/api/soundcheck", "", dry_run=True)
        mock_urlopen.assert_not_called()

    def test_uses_user_group_for_checks_and_track(self):
        assert OWNER == "group:redhat/ai5-marketplace"
        assert TRACK_DEFINITION["ownerEntityRef"] == OWNER
        assert all(check["ownerEntityRef"] == OWNER for check in ALL_CHECK_DEFS)

    def test_bulk_check_registration_posts_all_definitions(self):
        response = MagicMock()
        response.status = 201
        with patch(
            "scripts.mcp.bootstrap_compass_track.urllib.request.urlopen",
            return_value=response,
        ) as mock_urlopen:
            assert _register_checks(
                "https://compass.example.com/api/soundcheck",
                ALL_CHECK_DEFS,
                "test-key",
                dry_run=False,
            )
        request = mock_urlopen.call_args.args[0]
        assert request.full_url == "https://compass.example.com/api/soundcheck/checks"
        assert request.method == "POST"
        assert json.loads(request.data)["checks"] == ALL_CHECK_DEFS

    def test_bulk_check_dry_run_does_not_make_http_request(self):
        with patch("scripts.mcp.bootstrap_compass_track.urllib.request.urlopen") as mock_urlopen:
            assert _register_checks(
                "https://compass.example.com/api/soundcheck",
                ALL_CHECK_DEFS,
                "",
                dry_run=True,
            )
        mock_urlopen.assert_not_called()


# ---------------------------------------------------------------------------
# compass_publish.py — Payload tests
# ---------------------------------------------------------------------------


class TestBuildCheckFactPayload:
    def test_payload_shape(self):
        check = McpCheckResult(
            check_id="mcp-secrets", name="Secrets", passed=True, score=1.0, status="pass", message="Clean"
        )
        payload = _build_check_fact_payload(check, "mcpserver:default/test", "abevalflow:default/mcp_secrets")
        assert len(payload["facts"]) == 1
        fact = payload["facts"][0]
        assert fact["factRef"] == "abevalflow:default/mcp_secrets"
        assert fact["entityRef"] == "mcpserver:default/test"
        assert fact["data"]["passed"] is True
        assert fact["data"]["score"] == 1.0
        assert fact["data"]["status"] == "pass"
        assert "evaluated_at" in fact["data"]


class TestBuildPhaseSummaryPayload:
    def test_includes_all_checks(self, tmp_path):
        path = _write_summary(tmp_path / "phase2-summary.json", PHASE2_SUMMARY_MIXED)
        phase = load_phase2_summary(path)
        payload = _build_phase_summary_payload(phase, "mcpserver:default/test", "abevalflow:default/mcp_phase2")
        data = payload["facts"][0]["data"]
        assert data["phase"] == "phase2"
        assert data["checks_total"] == 10
        assert data["checks_evaluated"] == 7
        check_ids = [c["check_id"] for c in data["checks"]]
        assert "tool-name-rules" in check_ids
        assert "oauth-catalog-match" in check_ids
        assert "http-support" in check_ids


class TestBuildMcpSummaryPayload:
    def test_without_phase3(self):
        p1 = McpPhaseResult(phase=McpPhase.PHASE1, passed=True, checks=[])
        p2 = McpPhaseResult(phase=McpPhase.PHASE2, passed=False, checks=[])
        result = McpEvalResult(phase1=p1, phase2=p2)
        payload = _build_mcp_summary_payload(result, "mcpserver:default/test", MCP_EVAL_SUMMARY_FACT)
        data = payload["facts"][0]["data"]
        assert data["phase1_passed"] is True
        assert data["phase2_passed"] is False
        assert data["phase3_passed"] is None
        assert data["phase3_score"] is None

    def test_with_phase3(self):
        p1 = McpPhaseResult(phase=McpPhase.PHASE1, passed=True, checks=[])
        p2 = McpPhaseResult(phase=McpPhase.PHASE2, passed=True, checks=[])
        p3 = McpPhaseResult(phase=McpPhase.PHASE3, passed=True, checks=[])
        result = McpEvalResult(phase1=p1, phase2=p2, phase3=p3)
        payload = _build_mcp_summary_payload(result, "mcpserver:default/test", MCP_EVAL_SUMMARY_FACT)
        data = payload["facts"][0]["data"]
        assert data["phase3_passed"] is True
        assert data["phase3_score"] == 0.0


# ---------------------------------------------------------------------------
# compass_publish.py — Push orchestration tests
# ---------------------------------------------------------------------------


def _mock_urlopen_200():
    """Create a mock for urllib.request.urlopen that returns 200."""
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


class TestPushMcpEvalFactsDryRun:
    def test_dry_run_no_http(self, tmp_path):
        p1_path = _write_summary(tmp_path / "phase1-summary.json", PHASE1_SUMMARY_ALL_PASS)
        p2_path = _write_summary(tmp_path / "phase2-summary.json", PHASE2_SUMMARY_MIXED)
        p1 = load_phase1_summary(p1_path)
        p2 = load_phase2_summary(p2_path)
        result = build_mcp_eval_result(p1, p2)

        config = McpPushConfig(
            endpoint="https://compass.example.com/api/soundcheck/facts/",
            entity_ref="mcpserver:default/test",
            dry_run=True,
        )

        with patch("abevalflow.compass_facts.urllib.request.urlopen") as mock_urlopen:
            results = push_mcp_eval_facts(result, config)
            mock_urlopen.assert_not_called()

        assert all(r.success for r in results)
        assert len(results) > 0


class TestConsumedChecksNotPublished:
    def test_consumed_excluded_from_individual_facts(self, tmp_path):
        p1_path = _write_summary(tmp_path / "phase1-summary.json", PHASE1_SUMMARY_ALL_PASS)
        p2_path = _write_summary(tmp_path / "phase2-summary.json", PHASE2_SUMMARY_MIXED)
        p1 = load_phase1_summary(p1_path)
        p2 = load_phase2_summary(p2_path)
        result = build_mcp_eval_result(p1, p2)

        config = McpPushConfig(
            endpoint="https://compass.example.com/api/soundcheck/facts/",
            entity_ref="mcpserver:default/test",
            dry_run=True,
        )

        results = push_mcp_eval_facts(result, config)

        # Three Phase 1 checks + five evaluated Phase 2 checks + two phase summaries + overall.
        expected_count = len(PHASE1_FACT_MAP) + 5 + 2 + 1
        assert len(results) == expected_count

        fact_refs = [r.fact_ref for r in results]
        for consumed_check in CONSUMED_CHECKS:
            for phase_map in ALL_FACT_MAPS.values():
                assert consumed_check not in phase_map, "consumed checks should not appear in fact maps"

        assert "abevalflow:default/mcp_rate_limiting" not in fact_refs
        assert "abevalflow:default/mcp_response_size_limit" not in fact_refs
        assert "abevalflow:default/mcp_phase2" in fact_refs


class TestPushMcpEvalFactsLive:
    def test_publish_calls_urlopen(self, tmp_path):
        p1_path = _write_summary(tmp_path / "phase1-summary.json", PHASE1_SUMMARY_ALL_PASS)
        p2_path = _write_summary(tmp_path / "phase2-summary.json", PHASE2_SUMMARY_MIXED)
        p1 = load_phase1_summary(p1_path)
        p2 = load_phase2_summary(p2_path)
        result = build_mcp_eval_result(p1, p2)

        config = McpPushConfig(
            endpoint="https://compass.example.com/api/soundcheck/facts/",
            entity_ref="mcpserver:default/test",
            bearer_token="test-token",
            dry_run=False,
        )

        payloads_sent: list[dict] = []

        def capture_request(req, **kwargs):
            data = json.loads(req.data.decode("utf-8"))
            payloads_sent.append(data)
            return _mock_urlopen_200()

        with patch("abevalflow.compass_facts.urllib.request.urlopen", side_effect=capture_request):
            results = push_mcp_eval_facts(result, config)

        assert all(r.success for r in results)
        expected_count = len(PHASE1_FACT_MAP) + 5 + 2 + 1
        assert len(payloads_sent) == expected_count

        all_fact_refs = [p["facts"][0]["factRef"] for p in payloads_sent]
        for consumed_check in CONSUMED_CHECKS:
            consumed_fact_refs = [v for m in ALL_FACT_MAPS.values() for k, v in m.items() if k == consumed_check]
            for ref in consumed_fact_refs:
                assert ref not in all_fact_refs

        assert MCP_EVAL_SUMMARY_FACT in all_fact_refs
        for phase_fact in PHASE_SUMMARY_FACTS.values():
            if phase_fact == PHASE_SUMMARY_FACTS["phase3"]:
                continue
            assert phase_fact in all_fact_refs
