"""Tests for generic MCP Compass publishing."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from abevalflow.mcp.checks import compass_publish
from abevalflow.mcp.checks.compass_publish import (
    MCP_EVAL_SUMMARY_FACT,
    PHASE1_FACT_MAP,
    PHASE2_FACT_MAP,
    PHASE_FACT_MAPS,
    PHASE_SUMMARY_FACTS,
    McpPushConfig,
    _build_check_fact_payload,
    _build_mcp_summary_payload,
    _build_phase_summary_payload,
    push_mcp_eval_facts,
)
from abevalflow.mcp.eval_result import (
    PHASE_CHECK_NAMES,
    PHASE_CONSUMED_CHECKS,
    PHASE_SOURCE_DEFAULTS,
    McpCheckResult,
    McpEvalResult,
    McpPhaseResult,
    build_mcp_eval_result,
    load_phase_summary,
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
from scripts.mcp.runners import compass_publish as publish_runner
from scripts.mcp.runners.compass_publish import (
    _load_summaries,
    _phase_from_filename,
    _summary_files,
)

PHASE1_SUMMARY = {
    "phase": "static-eval",
    "passed": False,
    "checks": [
        {
            "check": "mcp-secrets",
            "passed": False,
            "status": "fail",
            "score": 0.25,
            "findings": 2,
            "message": "2 secrets found",
        },
        {
            "check": "mcp-no-user-code",
            "passed": True,
            "status": "pass",
            "score": 1.0,
            "findings": 0,
            "message": "Clean",
        },
    ],
}

PHASE2_SUMMARY = {
    "phase": "conformance-eval",
    "passed": False,
    "checks": [
        {
            "check": "http-support",
            "status": "pass",
            "passed": True,
            "score": 1.0,
            "source": "probe",
            "message": "HTTP OK",
        },
        {
            "check": "rate-limiting",
            "status": "not_evaluated",
            "passed": True,
            "score": 1.0,
            "source": "probe",
            "message": "Deferred",
        },
        {
            "check": "tool-name-rules",
            "status": "pass",
            "passed": True,
            "score": 1.0,
            "source": "compass",
            "message": "Tool names valid",
        },
        {
            "check": "oauth-catalog-match",
            "status": "fail",
            "passed": False,
            "score": 0.0,
            "source": "compass",
            "message": "OAuth mismatch",
        },
    ],
}


def _write_summary(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    return path


def _phase(name: str, passed: bool = True, check_id: str = "check") -> McpPhaseResult:
    check = McpCheckResult(check_id=check_id, name=check_id, passed=passed, score=1.0, status="pass")
    return McpPhaseResult(phase=name, passed=passed, checks=[check])


class TestMcpCheckResult:
    def test_defaults(self):
        check = McpCheckResult(check_id="x", name="X", passed=True, score=1.0, status="pass")
        assert check.source == "pipeline"

    def test_consumed_checks_are_configured_per_phase(self):
        assert PHASE_CONSUMED_CHECKS["conformance-eval"] == {"tool-name-rules", "oauth-catalog-match"}
        assert "phase1" not in PHASE_CONSUMED_CHECKS


class TestMcpPhaseResult:
    def test_computed_fields(self):
        checks = [
            McpCheckResult(check_id="a", name="A", passed=True, score=1.0, status="pass"),
            McpCheckResult(check_id="b", name="B", passed=False, score=0.0, status="fail"),
            McpCheckResult(check_id="c", name="C", passed=None, score=None, status="not_evaluated"),
        ]
        phase = McpPhaseResult(phase="phase-custom", passed=False, checks=checks)
        assert phase.checks_passed == 1
        assert phase.checks_total == 3
        assert phase.checks_evaluated == 2
        assert phase.overall_score == pytest.approx(0.5)

    def test_empty_checks(self):
        phase = McpPhaseResult(phase="phase4", passed=True, checks=[])
        assert phase.overall_score == 0.0
        assert phase.checks_passed == 0


class TestMcpEvalResult:
    def test_dynamic_phases_passed(self):
        result = build_mcp_eval_result([_phase("phase2", False), _phase("phase4")])
        assert result.phases_passed == {"phase2": False, "phase4": True}
        assert set(result.phases) == {"phase2", "phase4"}

    def test_build_two_and_three_phases(self):
        two = build_mcp_eval_result([_phase("phase1"), _phase("phase2")])
        three = build_mcp_eval_result([_phase("phase1"), _phase("phase2"), _phase("phase3")])
        assert len(two.phases) == 2
        assert len(three.phases) == 3

    def test_rejects_empty_or_duplicate_phase_set(self):
        with pytest.raises(ValidationError, match="at least one phase"):
            build_mcp_eval_result([])
        with pytest.raises(ValueError, match="Duplicate phase"):
            build_mcp_eval_result([_phase("phase2"), _phase("phase2")])

    def test_phase_mapping_key_must_match_name(self):
        with pytest.raises(ValidationError, match="does not match"):
            McpEvalResult(phases={"phase2": _phase("phase4")})


class TestLoadPhaseSummary:
    def test_loads_standard_phase1_checks(self, tmp_path):
        path = _write_summary(tmp_path / "static-eval-summary.json", PHASE1_SUMMARY)
        result = load_phase_summary(path)
        assert result.phase == "static-eval"
        assert result.checks_total == 2
        assert result.checks[0].name == "Secrets Management"
        assert result.checks[0].status == "fail"
        assert result.checks[0].source == "pipeline"

    def test_loads_phase2_three_state_and_consumed_checks(self, tmp_path):
        path = _write_summary(tmp_path / "phase2-summary.json", PHASE2_SUMMARY)
        result = load_phase_summary(path)
        checks = {check.check_id: check for check in result.checks}
        assert checks["rate-limiting"].passed is None
        assert checks["rate-limiting"].score is None
        assert checks["tool-name-rules"].source == "compass"
        assert checks["tool-name-rules"].check_id in PHASE_CONSUMED_CHECKS["conformance-eval"]
        assert checks["oauth-catalog-match"].check_id in PHASE_CONSUMED_CHECKS["conformance-eval"]
        assert checks["http-support"].check_id not in PHASE_CONSUMED_CHECKS["conformance-eval"]
        assert result.checks_evaluated == 3
        assert result.overall_score == pytest.approx(2 / 3)

    def test_loads_unknown_phase_without_enum_failure(self, tmp_path):
        data = {"phase": "phase-custom", "passed": True, "checks": [{"check": "new-check", "passed": True}]}
        result = load_phase_summary(_write_summary(tmp_path / "phase-custom-summary.json", data))
        assert result.phase == "phase-custom"
        assert result.checks[0].name == "new-check"
        assert result.checks[0].source == "pipeline"

    def test_phase_config_controls_names_consumption_and_source(self, tmp_path, monkeypatch):
        monkeypatch.setitem(PHASE_CHECK_NAMES, "phase4", {"special": "Special Check"})
        monkeypatch.setitem(PHASE_CONSUMED_CHECKS, "phase4", {"special"})
        monkeypatch.setitem(PHASE_SOURCE_DEFAULTS, "phase4", "custom-runner")
        data = {"phase": "phase4", "passed": True, "checks": [{"check": "special", "passed": True}]}
        result = load_phase_summary(_write_summary(tmp_path / "phase4-summary.json", data))
        assert result.checks[0].name == "Special Check"
        assert result.checks[0].source == "custom-runner"
        assert result.checks[0].check_id in PHASE_CONSUMED_CHECKS["phase4"]

    def test_invalid_status_rejected(self, tmp_path):
        data = {"phase": "phase4", "passed": True, "checks": [{"check": "x", "passed": True, "status": "skip"}]}
        with pytest.raises(ValueError, match="Invalid status"):
            load_phase_summary(_write_summary(tmp_path / "phase4-summary.json", data))


class TestSummaryInputs:
    def test_extracts_dashed_phase_names(self):
        assert _phase_from_filename(Path("phase-custom-summary.json")) == "phase-custom"
        assert _phase_from_filename(Path("app-phase1-summary.json")) == "app-phase1"

    @pytest.mark.parametrize("filename", ["summary.json", "-summary.json", "bad name-summary.json"])
    def test_rejects_invalid_summary_names(self, filename):
        with pytest.raises(ValueError, match="filename|phase name"):
            _phase_from_filename(Path(filename))

    def test_explicit_paths_are_returned_as_given(self, tmp_path):
        path = _write_summary(tmp_path / "phase4-summary.json", {"phase": "phase4", "passed": True, "checks": []})
        assert _summary_files([path], None) == [path]

    def test_directory_discovery_is_recursive_and_scoped(self, tmp_path):
        report_dir = tmp_path / "submission-reports"
        expected = _write_summary(
            report_dir / "phase4" / "phase4-summary.json", {"phase": "phase4", "passed": True, "checks": []}
        )
        _write_summary(tmp_path / "other" / "phase5-summary.json", {"phase": "phase5", "passed": True, "checks": []})
        assert _summary_files(None, report_dir) == [expected]

    def test_empty_directory_fails(self, tmp_path):
        with pytest.raises(ValueError, match="No phase summary"):
            _summary_files(None, tmp_path)

    def test_load_rejects_filename_json_mismatch(self, tmp_path):
        path = _write_summary(tmp_path / "phase2-summary.json", {"phase": "phase4", "passed": True, "checks": []})
        with pytest.raises(ValueError, match="Phase mismatch"):
            _load_summaries([path])

    def test_load_rejects_duplicate_phase_names(self, tmp_path):
        data = {"phase": "phase4", "passed": True, "checks": []}
        first = _write_summary(tmp_path / "one" / "phase4-summary.json", data)
        second = _write_summary(tmp_path / "two" / "phase4-summary.json", data)
        with pytest.raises(ValueError, match="Duplicate phase"):
            _load_summaries([first, second])


@pytest.mark.parametrize("input_option", ["--reports-dir", "--summary-files"])
def test_cli_loads_phases_from_directory_or_explicit_files(tmp_path, monkeypatch, input_option):
    report_dir = tmp_path / "reports"
    summary = _write_summary(
        report_dir / "phase4" / "phase4-summary.json",
        {"phase": "phase4", "passed": True, "checks": []},
    )
    input_path = report_dir if input_option == "--reports-dir" else summary
    monkeypatch.setattr(
        "sys.argv",
        [
            "compass_publish",
            input_option,
            str(input_path),
            "--entity-ref",
            "mcpserver:default/test",
            "--compass-url",
            "https://compass.example.com/api/soundcheck/facts/",
        ],
    )
    monkeypatch.delenv("COMPASS_API_KEY", raising=False)
    seen: dict[str, set[str]] = {}

    def fake_push(eval_result, _config):
        seen["phases"] = set(eval_result.phases)
        return []

    monkeypatch.setattr(publish_runner, "push_mcp_eval_facts", fake_push)
    assert publish_runner.main() == 0
    assert seen["phases"] == {"phase4"}


class TestCompassTrackBootstrap:
    def test_track_is_separate_and_reuses_existing_compass_checks(self):
        assert TRACK_ID == "abevalflow-mcp-evaluation"
        assert len(TRACK_DEFINITION["levels"]) == 3
        phase1, phase2, phase3 = TRACK_DEFINITION["levels"]
        assert (phase1["ordinal"], phase1["name"]) == (1, "Phase 1 — Static Analysis")
        assert (phase2["ordinal"], phase2["name"]) == (2, "Phase 2 — Conformance Testing")
        assert (phase3["ordinal"], phase3["name"]) == (3, "Phase 3 — Behavioral Evaluation")
        phase1_ids = {check["id"] for check in phase1["checks"]}
        phase2_ids = {check["id"] for check in phase2["checks"]}
        phase3_ids = {check["id"] for check in phase3["checks"]}
        assert len(phase1_ids) == 3
        assert len(phase2_ids) == 10
        assert len(phase3_ids) == 3
        assert phase1_ids.isdisjoint(phase2_ids)
        assert phase1_ids.isdisjoint(phase3_ids)
        assert phase2_ids.isdisjoint(phase3_ids)
        assert "mcp-tool-schema-check" in phase2_ids
        assert "mcp-oauth-scope-match" in phase2_ids
        assert not any("summary" in check_id for check_id in phase1_ids | phase2_ids | phase3_ids)
        assert "mcp-eval-tool-name-rules" not in phase2_ids
        assert "mcp-eval-oauth-catalog-match" not in phase2_ids
        registered_ids = {check["id"] for check in ALL_CHECK_DEFS}
        assert registered_ids.isdisjoint({check["id"] for check in CONSUMED_TRACK_CHECKS})
        assert len(ALL_CHECK_DEFS) == 14

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
        with patch("scripts.mcp.bootstrap_compass_track.urllib.request.urlopen", return_value=response) as urlopen:
            assert _register_checks(
                "https://compass.example.com/api/soundcheck", ALL_CHECK_DEFS, "test-key", dry_run=False
            )
        request = urlopen.call_args.args[0]
        assert request.full_url == "https://compass.example.com/api/soundcheck/checks"
        assert request.method == "POST"
        assert json.loads(request.data)["checks"] == ALL_CHECK_DEFS

    def test_bulk_check_dry_run_does_not_make_http_request(self):
        with patch("scripts.mcp.bootstrap_compass_track.urllib.request.urlopen") as mock_urlopen:
            assert _register_checks("https://compass.example.com/api/soundcheck", ALL_CHECK_DEFS, "", dry_run=True)
        mock_urlopen.assert_not_called()


class TestPayloads:
    def test_check_fact_payload_shape(self):
        check = McpCheckResult(
            check_id="mcp-secrets", name="Secrets", passed=True, score=1.0, status="pass", message="Clean"
        )
        payload = _build_check_fact_payload(check, "mcpserver:default/test", "abevalflow:default/mcp_secrets")
        fact = payload["facts"][0]
        assert fact["factRef"] == "abevalflow:default/mcp_secrets"
        assert fact["entityRef"] == "mcpserver:default/test"
        assert fact["data"]["passed"] is True
        assert fact["data"]["score"] == 1.0
        assert fact["data"]["status"] == "pass"
        assert "evaluated_at" in fact["data"]

    def test_phase_summary_includes_all_checks(self, tmp_path):
        phase = load_phase_summary(_write_summary(tmp_path / "conformance-eval-summary.json", PHASE2_SUMMARY))
        payload = _build_phase_summary_payload(phase, "mcpserver:default/test", "abevalflow:default/mcp_phase2")
        data = payload["facts"][0]["data"]
        assert data["phase"] == "conformance-eval"
        assert data["checks_total"] == 4
        assert data["checks_evaluated"] == 3
        assert {check["check_id"] for check in data["checks"]} == {
            "http-support",
            "rate-limiting",
            "tool-name-rules",
            "oauth-catalog-match",
        }

    def test_cross_phase_summary_uses_generic_map(self):
        result = build_mcp_eval_result([_phase("phase2", False), _phase("phase4")])
        payload = _build_mcp_summary_payload(result, "mcpserver:default/test", MCP_EVAL_SUMMARY_FACT)
        data = payload["facts"][0]["data"]
        assert data["phases"]["phase2"] == {
            "passed": False,
            "score": 1.0,
            "checks_evaluated": 1,
            "checks_total": 1,
        }
        assert data["phases"]["phase4"]["passed"] is True
        assert "phase1_passed" not in data
        assert "evaluated_at" in data


def _mock_urlopen_200():
    response = MagicMock()
    response.status = 200
    response.__enter__ = lambda _self: response
    response.__exit__ = MagicMock(return_value=False)
    return response


class TestPushMcpEvalFacts:
    def test_dynamic_iteration_and_consumed_checks(self, tmp_path, monkeypatch):
        phase1 = load_phase_summary(_write_summary(tmp_path / "phase1-summary.json", PHASE1_SUMMARY))
        phase2 = load_phase_summary(_write_summary(tmp_path / "phase2-summary.json", PHASE2_SUMMARY))
        phase4 = _phase("phase4", check_id="phase4-check")
        monkeypatch.setitem(compass_publish.PHASE_FACT_MAPS, "phase4", {"phase4-check": "test:default/phase4_check"})
        monkeypatch.setitem(compass_publish.PHASE_SUMMARY_FACTS, "phase4", "test:default/phase4_summary")
        result = build_mcp_eval_result([phase1, phase2, phase4])
        config = McpPushConfig(
            endpoint="https://compass.example.com/api/soundcheck/facts/",
            entity_ref="mcpserver:default/test",
            dry_run=True,
        )
        with patch("abevalflow.compass_facts.urllib.request.urlopen") as urlopen:
            results = push_mcp_eval_facts(result, config)
            urlopen.assert_not_called()
        refs = [entry.fact_ref for entry in results]
        assert "test:default/phase4_check" in refs
        assert "test:default/phase4_summary" in refs
        assert len(refs) == 2 + 1 + 1 + 3 + 1
        assert all("tool-name-rules" not in ref and "oauth-catalog-match" not in ref for ref in refs)
        assert MCP_EVAL_SUMMARY_FACT in refs

    def test_missing_fact_mappings_are_skipped(self, caplog):
        result = build_mcp_eval_result([_phase("unconfigured", check_id="unmapped")])
        config = McpPushConfig(endpoint="https://compass.example.com", entity_ref="mcpserver:default/test")
        results = push_mcp_eval_facts(result, config)
        assert [entry.fact_ref for entry in results] == [MCP_EVAL_SUMMARY_FACT]
        assert "No factRef mapping" in caplog.text
        assert "No phase summary factRef mapping" in caplog.text

    def test_live_publish_posts_facts(self, tmp_path):
        phase1 = load_phase_summary(_write_summary(tmp_path / "phase1-summary.json", PHASE1_SUMMARY))
        phase2 = load_phase_summary(_write_summary(tmp_path / "phase2-summary.json", PHASE2_SUMMARY))
        result = build_mcp_eval_result([phase1, phase2])
        config = McpPushConfig(
            endpoint="https://compass.example.com/api/soundcheck/facts/",
            entity_ref="mcpserver:default/test",
            bearer_token="test-token",
            dry_run=False,
        )
        payloads: list[dict] = []

        def capture_request(request, **_kwargs):
            payloads.append(json.loads(request.data.decode("utf-8")))
            return _mock_urlopen_200()

        with patch("abevalflow.compass_facts.urllib.request.urlopen", side_effect=capture_request):
            results = push_mcp_eval_facts(result, config)

        assert all(entry.success for entry in results)
        assert len(payloads) == len(results)
        refs = [payload["facts"][0]["factRef"] for payload in payloads]
        assert MCP_EVAL_SUMMARY_FACT in refs
        assert PHASE_SUMMARY_FACTS["static-eval"] in refs
        assert PHASE_SUMMARY_FACTS["conformance-eval"] in refs
        assert "abevalflow:default/mcp_rate_limiting" not in refs
        assert "abevalflow:default/mcp_phase2" in refs


def test_phase_config_keeps_existing_fact_maps():
    assert PHASE_FACT_MAPS["static-eval"] is PHASE1_FACT_MAP
    assert PHASE_FACT_MAPS["conformance-eval"] is PHASE2_FACT_MAP
