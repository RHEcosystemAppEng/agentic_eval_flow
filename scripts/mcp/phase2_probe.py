#!/usr/bin/env python3
"""MCP Phase 2 live conformance probe.

Connects to a running MCP server over Streamable HTTP and runs the deterministic
(no-LLM) contract/conformance checks that require a live server, writing one
three-state result file per check for the Phase 2 gates to read.

Checks that Compass already collects as real automated facts (tool-name rules,
OAuth match) or as metadata (schema present, documentation) are NOT probed here -
they are consumed via ``scripts/mcp/compass_fetch.py`` instead.

Usage:
    python -m scripts.mcp.phase2_probe --server-url http://<service>:8080/mcp \
        --reports-dir <dir> [--timeout 30]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from scripts.mcp._common import make_finding
from scripts.mcp._mcp_client import MCPClient, MCPConnectionError, RpcResponse
from scripts.mcp._phase2 import (
    STATUS_FAIL,
    STATUS_NOT_EVALUATED,
    STATUS_PASS,
    CheckOutcome,
    write_check_result,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Individual checks. Each returns a CheckOutcome.
# ---------------------------------------------------------------------------


def check_http_support(client: MCPClient, init: RpcResponse) -> CheckOutcome:
    """Server responds correctly over HTTP with a parseable JSON-RPC message."""
    findings = []
    if init.status_code != 200:
        findings.append(
            make_finding(
                severity="high",
                message=f"initialize returned HTTP {init.status_code}, expected 200",
                rule_id="http-status",
            )
        )
    if init.message is None:
        findings.append(
            make_finding(
                severity="high",
                message="initialize response body was not a parseable JSON-RPC message (json or SSE)",
                rule_id="http-body",
            )
        )
    if findings:
        return CheckOutcome(
            "http-support", STATUS_FAIL, "Server did not respond correctly over HTTP.", findings=findings
        )
    return CheckOutcome("http-support", STATUS_PASS, f"Server responded over HTTP (content-type: {init.content_type}).")


def check_protocol_compliance(client: MCPClient, init: RpcResponse) -> CheckOutcome:
    """initialize result, capability negotiation, JSON-RPC 2.0 correctness."""
    findings = []
    msg = init.message if isinstance(init.message, dict) else {}

    if msg.get("jsonrpc") != "2.0":
        findings.append(
            make_finding(
                severity="high",
                message=f"initialize response jsonrpc={msg.get('jsonrpc')!r}, expected '2.0'",
                rule_id="jsonrpc-version",
            )
        )
    if msg.get("id") != 1:
        findings.append(
            make_finding(
                severity="high",
                message=f"initialize response id={msg.get('id')!r} did not echo request id 1",
                rule_id="id-echo",
            )
        )

    result = msg.get("result", {}) if isinstance(msg.get("result"), dict) else {}
    for required in ("protocolVersion", "capabilities", "serverInfo"):
        if required not in result:
            findings.append(
                make_finding(
                    severity="high",
                    message=f"InitializeResult missing '{required}' (capability negotiation)",
                    rule_id="init-result",
                )
            )

    # Unknown method must yield a JSON-RPC error (ideally -32601 method not found).
    try:
        unknown = client.post({"jsonrpc": "2.0", "id": 90, "method": "no/such/method", "params": {}})
        err = unknown.message.get("error") if isinstance(unknown.message, dict) else None
        if not err:
            findings.append(
                make_finding(
                    severity="medium",
                    message="unknown method did not return a JSON-RPC error object",
                    rule_id="error-unknown-method",
                )
            )
        elif err.get("code") != -32601:
            findings.append(
                make_finding(
                    severity="low",
                    message=f"unknown method returned error code {err.get('code')}, expected -32601",
                    rule_id="error-code",
                )
            )

        # Malformed request (bad jsonrpc version) must not be accepted as success.
        bad = client.post({"jsonrpc": "1.0", "id": 91, "method": "tools/list", "params": {}})
        bad_err = bad.message.get("error") if isinstance(bad.message, dict) else None
        if bad.status_code == 200 and not bad_err:
            findings.append(
                make_finding(
                    severity="medium",
                    message="malformed request (jsonrpc 1.0) was accepted without an error",
                    rule_id="error-malformed",
                )
            )
    except MCPConnectionError as exc:
        return CheckOutcome("protocol-compliance", STATUS_NOT_EVALUATED, f"Server became unreachable mid-probe: {exc}")

    if findings:
        return CheckOutcome(
            "protocol-compliance", STATUS_FAIL, "Protocol/JSON-RPC 2.0 conformance violations found.", findings=findings
        )
    return CheckOutcome(
        "protocol-compliance",
        STATUS_PASS,
        "initialize, capability negotiation, and JSON-RPC 2.0 error handling conform.",
    )


def _tools_from(tools_resp: RpcResponse) -> list[dict]:
    if isinstance(tools_resp.message, dict):
        result = tools_resp.message.get("result", {})
        if isinstance(result, dict):
            tools = result.get("tools", [])
            if isinstance(tools, list):
                return [t for t in tools if isinstance(t, dict)]
    return []


def _schema_is_object(schema: object) -> bool:
    """A JSON-Schema-ish object: declares ``type: object`` or carries ``properties``."""
    return isinstance(schema, dict) and (schema.get("type") == "object" or "properties" in schema)


def check_schema_conformance(client: MCPClient, tools_resp: RpcResponse) -> CheckOutcome:
    """Every tool declares a well-formed input schema, plus a well-formed output
    schema when one is present.

    inputSchema is required by the MCP tool type, so a missing/non-object one is a
    fail. outputSchema is optional in the MCP spec, so it is validated only when
    present (its absence is not a violation). Whether actual tool RESPONSES conform
    to the declared schema is behavioral and is left to Phase 3.
    """
    tools = _tools_from(tools_resp)
    if not tools:
        return CheckOutcome("schema-conformance", STATUS_NOT_EVALUATED, "tools/list returned no tools to validate.")
    findings = []
    for tool in tools:
        name = tool.get("name", "<unnamed>")
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict):
            findings.append(
                make_finding(
                    severity="high", message=f"tool '{name}' has no object inputSchema", rule_id="schema-missing"
                )
            )
        elif not _schema_is_object(schema):
            findings.append(
                make_finding(
                    severity="medium",
                    message=f"tool '{name}' inputSchema is not a JSON-Schema object (no type/properties)",
                    rule_id="schema-shape",
                )
            )
        # outputSchema is optional; validate its shape only when the tool declares one.
        out_schema = tool.get("outputSchema")
        if out_schema is not None and not _schema_is_object(out_schema):
            findings.append(
                make_finding(
                    severity="medium",
                    message=f"tool '{name}' outputSchema is present but not a JSON-Schema object",
                    rule_id="output-schema-shape",
                )
            )
    if findings:
        return CheckOutcome(
            "schema-conformance", STATUS_FAIL, "One or more tool schemas are not well-formed.", findings=findings
        )
    return CheckOutcome("schema-conformance", STATUS_PASS, f"All {len(tools)} tool schemas are well-formed.")


_HINT_FIELDS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def check_tool_annotations(client: MCPClient, tools_resp: RpcResponse) -> CheckOutcome:
    """Validate tool behavior-hint annotations when present.

    Annotations are OPTIONAL in the MCP spec, so their absence is not a
    conformance violation: a server that declares none is not_evaluated here
    (nothing to verify), never failed. Only a *malformed* annotation (a hint
    field present with a non-boolean value) is a real fail. Honesty of the hints
    is behavioral and is left to Phase 3.
    """
    tools = _tools_from(tools_resp)
    if not tools:
        return CheckOutcome("tool-annotations", STATUS_NOT_EVALUATED, "tools/list returned no tools to inspect.")

    findings = []
    annotated = 0
    for tool in tools:
        name = tool.get("name", "<unnamed>")
        annotations = tool.get("annotations")
        if not isinstance(annotations, dict):
            continue
        present_hints = [h for h in _HINT_FIELDS if h in annotations]
        if present_hints:
            annotated += 1
        for hint in present_hints:
            if not isinstance(annotations[hint], bool):
                findings.append(
                    make_finding(
                        severity="medium",
                        message=f"tool '{name}' annotation '{hint}' is not a boolean",
                        rule_id="annotation-malformed",
                    )
                )

    if findings:
        return CheckOutcome(
            "tool-annotations", STATUS_FAIL, "One or more tool annotations are malformed.", findings=findings
        )
    if annotated == 0:
        return CheckOutcome(
            "tool-annotations",
            STATUS_NOT_EVALUATED,
            "No tools declare behavior-hint annotations (optional in the MCP spec); nothing to verify.",
        )
    return CheckOutcome("tool-annotations", STATUS_PASS, f"All {annotated} annotated tool(s) have well-formed hints.")


def check_rate_limiting(client: MCPClient, *, burst: int = 20) -> CheckOutcome:
    """Send a burst of requests; pass if the server throttles (HTTP 429)."""
    try:
        statuses = [client.list_tools(request_id=1000 + i).status_code for i in range(burst)]
    except MCPConnectionError as exc:
        return CheckOutcome("rate-limiting", STATUS_NOT_EVALUATED, f"Server unreachable during burst: {exc}")
    if 429 in statuses:
        return CheckOutcome(
            "rate-limiting", STATUS_PASS, f"Server returned HTTP 429 under a burst of {burst} requests."
        )
    return CheckOutcome(
        "rate-limiting",
        STATUS_NOT_EVALUATED,
        f"No throttling observed under a burst of {burst}; cannot tell 'no limit' from a limit above the burst.",
    )


def check_response_size_limit(client: MCPClient) -> CheckOutcome:
    """Response-size capping is not determinable black-box without a large-output tool + declared cap."""
    return CheckOutcome(
        "response-size-limit",
        STATUS_NOT_EVALUATED,
        "Requires a tool designed to elicit large output and a declared size cap; not determinable black-box.",
    )


def check_mandatory_timeouts(client: MCPClient) -> CheckOutcome:
    """Server-side timeout enforcement is not observable black-box without a hanging tool."""
    return CheckOutcome(
        "mandatory-timeouts",
        STATUS_NOT_EVALUATED,
        "Server-side timeout enforcement is not observable black-box without a tool that intentionally hangs.",
    )


def check_openapi_conformance(client: MCPClient) -> CheckOutcome:
    """OpenAPI conformance - deferred (not_evaluated).

    The ADR wants requests/responses validated against the server's OpenAPI
    contract (ADR lines 103, 209, 257), but two prerequisites are missing: no MCP
    repo publishes a contract at the standardized location yet, and the
    OpenAPI-operation <-> MCP-tool/method mapping is undefined. Wiring a validator
    before both exist would invent that convention, so this stays not_evaluated
    (never a fail) until they are settled.
    """
    return CheckOutcome(
        "openapi-conformance",
        STATUS_NOT_EVALUATED,
        "No OpenAPI contract provided and the contract<->tool mapping is unspecified; "
        "deferred until an MCP repo publishes a contract at the standardized location.",
    )


def probe_all(server_url: str, *, timeout: float = 30.0) -> list[CheckOutcome]:
    """Run every live Phase 2 check against ``server_url``.

    If the server is unreachable, all checks are reported not_evaluated (ADR:
    a readiness/reachability failure must never be scored as a fail).
    """
    live_names = [
        "http-support",
        "protocol-compliance",
        "schema-conformance",
        "tool-annotations",
        "rate-limiting",
        "response-size-limit",
        "mandatory-timeouts",
        "openapi-conformance",
    ]
    with MCPClient(server_url, timeout=timeout) as client:
        try:
            init = client.initialize()
            client.send_initialized()
            tools_resp = client.list_tools()
        except MCPConnectionError as exc:
            reason = f"Server unreachable at {server_url}: {exc}"
            logger.warning(reason)
            return [CheckOutcome(name, STATUS_NOT_EVALUATED, reason) for name in live_names]

        return [
            check_http_support(client, init),
            check_protocol_compliance(client, init),
            check_schema_conformance(client, tools_resp),
            check_tool_annotations(client, tools_resp),
            check_rate_limiting(client),
            check_response_size_limit(client),
            check_mandatory_timeouts(client),
            check_openapi_conformance(client),
        ]


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP Phase 2 live conformance probe")
    parser.add_argument("--server-url", required=True, help="MCP endpoint URL, e.g. http://<service>:8080/mcp")
    parser.add_argument(
        "--reports-dir", type=Path, required=True, help="Directory to write <check>-check.json files into"
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request HTTP timeout in seconds")
    args = parser.parse_args()

    start = time.monotonic()
    outcomes = probe_all(args.server_url, timeout=args.timeout)
    for outcome in outcomes:
        write_check_result(args.reports_dir, outcome)
    logger.info("Phase 2 probe complete: %d checks in %.1fs", len(outcomes), time.monotonic() - start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
