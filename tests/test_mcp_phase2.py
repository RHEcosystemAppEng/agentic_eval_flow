"""Tests for MCP Phase 2 (deterministic contract/conformance).

Unit tests use synthetic JSON-RPC responses and a fake client, so they need no
running server and are CI-safe. The integration test runs the real probe against
a live MCP server and is skipped unless MCP_TEST_SERVER_URL is set (e.g. a local
generic-mock-mcp-server).
"""

from __future__ import annotations

import json
import os

import pytest

from abevalflow.gates.base import GateMode
from abevalflow.mcp.phase2 import PHASE2_CHECKS, run_phase2
from abevalflow.mcp.phase2.base import Phase2Gate
from abevalflow.schemas import GatePolicy
from scripts.mcp import compass_fetch, phase2_probe, run_phase2_gates
from scripts.mcp._phase2 import STATUS_FAIL, STATUS_NOT_EVALUATED, STATUS_PASS, CheckOutcome, write_check_result
from scripts.mcp.mcp_client import RpcResponse, _parse_sse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rpc(message, *, status=200, content_type="application/json", headers=None):
    return RpcResponse(
        status_code=status,
        headers=headers or {},
        content_type=content_type,
        message=message,
        raw_text=json.dumps(message) if message is not None else "",
    )


class FakeClient:
    """Routes .post()/.list_tools() to canned RpcResponses by JSON-RPC method."""

    def __init__(self, by_method):
        self._by_method = by_method

    def post(self, body, **_kw):
        method = body.get("method") if isinstance(body, dict) else None
        resp = self._by_method.get(method)
        if resp is None:
            return _rpc({"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": -32601, "message": "not found"}})
        return resp

    def list_tools(self, request_id=2):
        return self._by_method["tools/list"]


# ---------------------------------------------------------------------------
# mcp_client
# ---------------------------------------------------------------------------


def test_parse_sse_extracts_json_rpc():
    body = 'event: message\ndata: {"jsonrpc": "2.0", "id": 1, "result": {}}\n\n'
    parsed = _parse_sse(body)
    assert parsed == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_parse_sse_returns_none_without_data():
    assert _parse_sse("event: ping\n\n") is None


# ---------------------------------------------------------------------------
# Probe checks (synthetic responses)
# ---------------------------------------------------------------------------


def _init_ok():
    return _rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "x"}},
        }
    )


def test_http_support_pass_and_fail():
    assert phase2_probe.check_http_support(None, _init_ok()).status == STATUS_PASS
    bad = _rpc(None, status=500, content_type="text/plain")
    out = phase2_probe.check_http_support(None, bad)
    assert out.status == STATUS_FAIL
    assert len(out.findings) == 2  # bad status + unparseable body


def test_protocol_compliance_pass():
    client = FakeClient(
        {
            "no/such/method": _rpc({"jsonrpc": "2.0", "id": 90, "error": {"code": -32601, "message": "nope"}}),
            "tools/list": _rpc({"jsonrpc": "2.0", "id": 91, "error": {"code": -32600, "message": "bad"}}),
        }
    )
    out = phase2_probe.check_protocol_compliance(client, _init_ok())
    assert out.status == STATUS_PASS, out.findings


def test_protocol_compliance_flags_missing_result_and_bad_id():
    init = _rpc({"jsonrpc": "2.0", "id": 999, "result": {"capabilities": {}}})  # wrong id, missing fields
    client = FakeClient(
        {
            "no/such/method": _rpc({"jsonrpc": "2.0", "id": 90, "error": {"code": -32601}}),
            "tools/list": _rpc({"jsonrpc": "2.0", "id": 91, "error": {"code": -32600}}),
        }
    )
    out = phase2_probe.check_protocol_compliance(client, init)
    assert out.status == STATUS_FAIL
    rule_ids = {f["rule_id"] for f in out.findings}
    assert "id-echo" in rule_ids
    assert "init-result" in rule_ids


def test_schema_conformance_pass_and_fail():
    good = _rpc({"result": {"tools": [{"name": "a", "inputSchema": {"type": "object", "properties": {}}}]}})
    assert phase2_probe.check_schema_conformance(None, good).status == STATUS_PASS

    bad = _rpc({"result": {"tools": [{"name": "b"}]}})  # no inputSchema
    out = phase2_probe.check_schema_conformance(None, bad)
    assert out.status == STATUS_FAIL

    empty = _rpc({"result": {"tools": []}})
    assert phase2_probe.check_schema_conformance(None, empty).status == STATUS_NOT_EVALUATED


def test_schema_conformance_output_schema_when_present():
    # Valid input + valid output -> pass.
    both = _rpc(
        {"result": {"tools": [{"name": "a", "inputSchema": {"type": "object"}, "outputSchema": {"type": "object"}}]}}
    )
    assert phase2_probe.check_schema_conformance(None, both).status == STATUS_PASS

    # Present-but-malformed output schema -> fail (optional, but must be well-formed when declared).
    bad_out = _rpc({"result": {"tools": [{"name": "b", "inputSchema": {"type": "object"}, "outputSchema": "nope"}]}})
    out = phase2_probe.check_schema_conformance(None, bad_out)
    assert out.status == STATUS_FAIL
    assert out.findings[0]["rule_id"] == "output-schema-shape"

    # No output schema at all -> not a violation (optional in the MCP spec).
    no_out = _rpc({"result": {"tools": [{"name": "c", "inputSchema": {"type": "object"}}]}})
    assert phase2_probe.check_schema_conformance(None, no_out).status == STATUS_PASS


def test_tool_annotations_absent_is_not_evaluated():
    # Annotations are optional in the MCP spec: none declared -> not_evaluated.
    resp = _rpc({"result": {"tools": [{"name": "a"}, {"name": "b"}]}})
    assert phase2_probe.check_tool_annotations(None, resp).status == STATUS_NOT_EVALUATED


def test_tool_annotations_wellformed_passes():
    resp = _rpc({"result": {"tools": [{"name": "a", "annotations": {"readOnlyHint": True}}, {"name": "b"}]}})
    assert phase2_probe.check_tool_annotations(None, resp).status == STATUS_PASS


def test_tool_annotations_malformed_fails():
    resp = _rpc({"result": {"tools": [{"name": "a", "annotations": {"readOnlyHint": "yes"}}]}})
    out = phase2_probe.check_tool_annotations(None, resp)
    assert out.status == STATUS_FAIL
    assert out.findings[0]["rule_id"] == "annotation-malformed"


def test_rate_limiting_pass_on_429():
    class Burst:
        def list_tools(self, request_id=0):
            return _rpc({}, status=429 if request_id == 1005 else 200)

    assert phase2_probe.check_rate_limiting(Burst(), burst=10).status == STATUS_PASS


def test_rate_limiting_not_evaluated_without_throttle():
    class Burst:
        def list_tools(self, request_id=0):
            return _rpc({}, status=200)

    assert phase2_probe.check_rate_limiting(Burst(), burst=5).status == STATUS_NOT_EVALUATED


def test_deferred_checks_are_not_evaluated():
    assert phase2_probe.check_response_size_limit(None).status == STATUS_NOT_EVALUATED
    assert phase2_probe.check_mandatory_timeouts(None).status == STATUS_NOT_EVALUATED
    assert phase2_probe.check_openapi_conformance(None).status == STATUS_NOT_EVALUATED


# ---------------------------------------------------------------------------
# Compass consume
# ---------------------------------------------------------------------------


def test_map_facts_pass():
    facts = {
        "mcp:default/tools": {"allToolNamesValid": True},
        "mcp:default/security": {
            "oauth": {"enforced": True},
            "entityAuth": {"authServerMatch": True},
            "scopeMatch": {"allScopesMatch": True},
        },
    }
    by_check = {o.check: o for o in compass_fetch.map_facts(facts)}
    assert set(by_check) == {"tool-name-rules", "oauth-catalog-match"}
    assert by_check["tool-name-rules"].status == STATUS_PASS
    assert by_check["oauth-catalog-match"].status == STATUS_PASS
    assert all(o.source == "compass" for o in by_check.values())


def test_map_facts_fail_and_missing():
    facts = {"mcp:default/tools": {"allToolNamesValid": False}}
    by_check = {o.check: o for o in compass_fetch.map_facts(facts)}
    assert by_check["tool-name-rules"].status == STATUS_FAIL
    assert by_check["oauth-catalog-match"].status == STATUS_NOT_EVALUATED  # no OAuth facts


def test_map_facts_oauth_partial_is_not_evaluated():
    # Only one OAuth subfield present -> cannot assert the conjunction -> not_evaluated,
    # never a fail (a partially-reported entity must not be blocked).
    facts = {"mcp:default/security": {"oauth": {"enforced": True}}}
    out = {o.check: o for o in compass_fetch.map_facts(facts)}["oauth-catalog-match"]
    assert out.status == STATUS_NOT_EVALUATED
    assert out.findings == []


def test_map_facts_oauth_explicit_false_fails_only_for_false():
    # An explicit False is a real violation; an absent sibling is not.
    facts = {
        "mcp:default/security": {
            "oauth": {"enforced": True},
            "entityAuth": {"authServerMatch": False},
            # scopeMatch absent
        }
    }
    out = {o.check: o for o in compass_fetch.map_facts(facts)}["oauth-catalog-match"]
    assert out.status == STATUS_FAIL
    assert {f["rule_id"] for f in out.findings} == {"oauth-server-match"}


def test_missing_facts_file_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "sys.argv",
        ["compass_fetch", "--facts-file", str(tmp_path / "nope.json"), "--reports-dir", str(tmp_path)],
    )
    assert compass_fetch.main() == 1


# ---------------------------------------------------------------------------
# Gates + runner (three-state)
# ---------------------------------------------------------------------------


def _write(tmp_path, check, status, findings=None):
    write_check_result(tmp_path, CheckOutcome(check, status, "reason", findings=findings or []))


def test_gate_pass_fail_not_evaluated(tmp_path):
    _write(tmp_path, "http-support", STATUS_PASS)
    _write(tmp_path, "protocol-compliance", STATUS_FAIL, [{"severity": "high", "message": "m", "rule_id": "r"}])
    _write(tmp_path, "openapi-conformance", STATUS_NOT_EVALUATED)
    # remaining checks have no file -> not_evaluated, must not fail

    policy = GatePolicy(default_mode=GateMode.BLOCK)
    results = {r.get_policy_key(): r for r in run_phase2(tmp_path, policy)}

    assert len(results) == len(PHASE2_CHECKS)
    assert results["http-support"].passed is True
    assert results["protocol-compliance"].passed is False  # fail in block mode
    assert results["openapi-conformance"].passed is True  # not_evaluated never penalized
    assert results["mandatory-timeouts"].details["status"] == STATUS_NOT_EVALUATED  # missing file


def test_corrupt_result_file_blocks_only_in_block_mode(tmp_path):
    (tmp_path / "http-support-check.json").write_text("{ not valid json ")
    gate = Phase2Gate("http-support")
    warn = gate.evaluate(tmp_path, GatePolicy(default_mode=GateMode.WARN))
    assert warn.details["status"] == STATUS_FAIL
    assert warn.passed is True  # warn never blocks, even on a corrupt file
    block = gate.evaluate(tmp_path, GatePolicy(default_mode=GateMode.BLOCK))
    assert block.passed is False


def test_fail_without_findings_scores_zero(tmp_path):
    # A bare fail (no findings) must not report a perfect score.
    write_check_result(tmp_path, CheckOutcome("http-support", STATUS_FAIL, "boom", findings=[]))
    res = Phase2Gate("http-support").evaluate(tmp_path, GatePolicy(default_mode=GateMode.BLOCK))
    assert res.passed is False
    assert res.score == 0.0


def test_not_evaluated_does_not_fail_runner(tmp_path, monkeypatch):
    _write(tmp_path, "http-support", STATUS_PASS)
    for check in PHASE2_CHECKS:
        if check != "http-support":
            _write(tmp_path, check, STATUS_NOT_EVALUATED)
    monkeypatch.setattr("sys.argv", ["run_phase2_gates", "--reports-dir", str(tmp_path), "--mode", "block"])
    assert run_phase2_gates.main() == 0
    summary = json.loads((tmp_path / "phase2-summary.json").read_text())
    assert summary["passed"] is True
    assert summary["counts"]["not_evaluated"] == len(PHASE2_CHECKS) - 1


def test_real_fail_exits_nonzero_in_block(tmp_path, monkeypatch):
    for check in PHASE2_CHECKS:
        _write(tmp_path, check, STATUS_PASS)
    _write(tmp_path, "protocol-compliance", STATUS_FAIL, [{"severity": "high", "message": "m", "rule_id": "r"}])
    monkeypatch.setattr("sys.argv", ["run_phase2_gates", "--reports-dir", str(tmp_path), "--mode", "block"])
    assert run_phase2_gates.main() == 1


# ---------------------------------------------------------------------------
# Integration (real server)
# ---------------------------------------------------------------------------

_SERVER_URL = os.environ.get("MCP_TEST_SERVER_URL")


@pytest.mark.skipif(not _SERVER_URL, reason="set MCP_TEST_SERVER_URL to a running MCP server")
def test_probe_against_live_server(tmp_path):
    # Smoke against a real server, e.g. generic-mock-mcp-server run with
    # --transport streamable-http on :8080 (endpoint http://localhost:8080/mcp).
    outcomes = phase2_probe.probe_all(_SERVER_URL, timeout=10.0)
    by_check = {o.check: o for o in outcomes}
    assert by_check["http-support"].status == STATUS_PASS
    assert by_check["protocol-compliance"].status in (STATUS_PASS, STATUS_FAIL)
