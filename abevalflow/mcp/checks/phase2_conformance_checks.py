"""Phase 2 live conformance check functions.

Each check takes a ``MCPClient`` (and/or the ``tools/list`` response) and returns
a ``CheckOutcome`` carrying a three-state result (pass / fail / not_evaluated).
``probe_all`` orchestrates every check against a running server.

These are pure evaluation logic: the client handles I/O, and the checks analyze
responses and return verdicts. The CLI entry point that creates the client and
writes results to disk lives in ``scripts/mcp/probes/phase2_conformance_probe.py``.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit, urlunsplit

from abevalflow.mcp.helpers import make_finding
from abevalflow.mcp.mcp_client import MCPClient, MCPConnectionError, RpcResponse
from abevalflow.mcp.types import (
    STATUS_FAIL,
    STATUS_NOT_EVALUATED,
    STATUS_PASS,
    CheckOutcome,
)

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
        elif not isinstance(err, dict):
            # A non-object "error" (e.g. a bare string) is itself a JSON-RPC
            # violation - and calling .get() on it would crash the probe.
            findings.append(
                make_finding(
                    severity="medium",
                    message=f"unknown method returned a non-object error ({type(err).__name__}): {err!r}",
                    rule_id="error-not-object",
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
        # A mid-probe disconnect must not erase conformance violations already found:
        # if we collected any, report them as a FAIL; only a disconnect with nothing
        # collected yet is genuinely not_evaluated.
        if findings:
            return CheckOutcome(
                "protocol-compliance",
                STATUS_FAIL,
                f"Protocol/JSON-RPC 2.0 conformance violations found before the server became "
                f"unreachable mid-probe: {exc}",
                findings=findings,
            )
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
    """A well-formed JSON-Schema object node.

    Requires the declared ``type`` to permit ``object`` (JSON Schema allows a type
    array like ``["object", "null"]``, so that is accepted; a conflicting type like
    ``{"type": "array", "properties": {}}`` is rejected), or, when no type is
    declared, at least one object keyword. Object keywords, when present, must have
    the right shape - ``properties`` / ``patternProperties`` must be mappings - so
    ``{"type": "object", "properties": []}`` is rejected too.
    """
    if not isinstance(schema, dict):
        return False
    declared_type = schema.get("type")
    # type may be a string or, per JSON Schema, a list of allowed types.
    type_permits_object = declared_type is None or "object" in (
        declared_type if isinstance(declared_type, list) else [declared_type]
    )
    if declared_type is not None and not type_permits_object:
        return False
    object_keywords = ("properties", "patternProperties", "additionalProperties")
    if declared_type is None and not any(k in schema for k in object_keywords):
        return False
    for mapping_keyword in ("properties", "patternProperties"):
        value = schema.get(mapping_keyword)
        if value is not None and not isinstance(value, dict):
            return False
    return True


def _tools_list_error(tools_resp: RpcResponse) -> str | None:
    """Describe why a tools/list response is unusable, or None when it is a
    well-formed result (including a well-formed *empty* tool list).

    Lets callers tell a broken tools/list endpoint (a conformance FAIL) from a
    server that simply declares no tools (not_evaluated), instead of collapsing
    both to an empty list.
    """
    msg = tools_resp.message
    if not isinstance(msg, dict):
        return "tools/list did not return a JSON-RPC object"
    if "error" in msg:
        return f"tools/list returned a JSON-RPC error: {msg.get('error')!r}"
    result = msg.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
        return "tools/list result has no well-formed 'tools' array"
    return None


def check_schema_conformance(client: MCPClient, tools_resp: RpcResponse) -> CheckOutcome:
    """Every tool declares a well-formed input schema, plus a well-formed output
    schema when one is present.

    inputSchema is required by the MCP tool type, so a missing/non-object one is a
    fail. outputSchema is optional in the MCP spec, so it is validated only when
    present (its absence is not a violation). Whether actual tool RESPONSES conform
    to the declared schema is behavioral and is left to Phase 3.
    """
    err = _tools_list_error(tools_resp)
    if err:
        # A broken tools/list endpoint is a real conformance failure, not an absence
        # of tools: fail rather than silently reporting not_evaluated.
        return CheckOutcome(
            "schema-conformance",
            STATUS_FAIL,
            err,
            findings=[make_finding(severity="high", message=err, rule_id="tools-list-malformed")],
        )
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
    err = _tools_list_error(tools_resp)
    if err:
        return CheckOutcome(
            "tool-annotations",
            STATUS_FAIL,
            err,
            findings=[make_finding(severity="high", message=err, rule_id="tools-list-malformed")],
        )
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


# Well-known locations to probe for an OpenAPI contract, relative to the server root.
_OPENAPI_WELL_KNOWN = (
    "/openapi.json",
    "/openapi.yaml",
    "/openapi",
    "/.well-known/openapi.json",
    "/swagger.json",
)


def _openapi_candidates(endpoint_url: str) -> list[str]:
    """Well-known contract URLs derived from the server root (scheme+host[:port])."""
    parts = urlsplit(endpoint_url)
    base = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    return [base + path for path in _OPENAPI_WELL_KNOWN]


def _parse_openapi(text: str) -> object:
    """Parse a contract body as JSON, then YAML; return None if neither works."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        import yaml  # baked into the eval image; optional locally

        return yaml.safe_load(text)
    except Exception:
        return None


def _is_openapi3(doc: object) -> bool:
    """Structurally a valid OpenAPI 3.x document: openapi 3.x + info + paths."""
    return (
        isinstance(doc, dict)
        and isinstance(doc.get("openapi"), str)
        and doc["openapi"].startswith("3.")
        and isinstance(doc.get("info"), dict)
        and isinstance(doc.get("paths"), dict)
    )


def _has_mcp_native_contract(tools_resp: RpcResponse | None) -> bool:
    """The server publishes an MCP-native contract: tools/list returned at least
    one tool with an ``inputSchema``.

    This is the "or equivalent" in the ADR requirement "OpenAPI (or equivalent)
    contract availability": MCP servers describe their callable surface via
    ``tools/list`` + JSON Schemas rather than a REST OpenAPI document, and that is
    a valid machine-readable API contract.
    """
    if tools_resp is None:
        return False
    tools = _tools_from(tools_resp)
    return any(isinstance(t.get("inputSchema"), dict) for t in tools)


def check_openapi_conformance(
    client: MCPClient,
    tools_resp: RpcResponse | None = None,
    *,
    openapi_url: str | None = None,
) -> CheckOutcome:
    """Server publishes an API contract: OpenAPI or the MCP-native equivalent.

    The ADR requires every HTTP MCP server to publish "an OpenAPI (or equivalent)
    contract describing its HTTP surface." For MCP servers the native equivalent is
    ``tools/list`` with well-formed ``inputSchema`` per tool - i.e. the standard
    MCP tool discovery mechanism IS the machine-readable contract.

    Check order:
    1. Probe for a REST OpenAPI/Swagger document (explicit URL or well-known paths).
       If found and valid -> PASS. Found but malformed -> FAIL.
    2. If no REST contract, fall back: does ``tools/list`` return at least one tool
       with an ``inputSchema``? -> PASS (MCP-native equivalent present).
    3. Neither -> FAIL (no discoverable contract at all).

    Deep request/response validation against the contract needs an agreed
    OpenAPI-operation <-> MCP-tool mapping and is left to a later iteration.
    """
    # 1. Try REST OpenAPI/Swagger contract.
    candidates = [openapi_url] if openapi_url else _openapi_candidates(client.endpoint_url)
    for url in candidates:
        try:
            status, _content_type, text = client.get(url)
        except MCPConnectionError:
            continue
        if status != 200 or not text.strip():
            continue
        doc = _parse_openapi(text)
        if doc is None:
            if openapi_url:
                # Explicit URL given by the operator — fail, don't silently skip.
                return CheckOutcome(
                    "openapi-conformance",
                    STATUS_FAIL,
                    f"Contract at {url} did not parse as JSON or YAML.",
                    findings=[
                        make_finding(
                            severity="high",
                            message=f"OpenAPI contract at {url} is not parseable",
                            rule_id="openapi-unparseable",
                        )
                    ],
                )
            # Well-known probe: could be a reverse-proxy health page or unrelated
            # content. Continue to the next candidate.
            continue
        if not _is_openapi3(doc):
            if openapi_url:
                return CheckOutcome(
                    "openapi-conformance",
                    STATUS_FAIL,
                    f"Document at {url} is not a valid OpenAPI 3.x contract.",
                    findings=[
                        make_finding(
                            severity="high",
                            message=f"Document at {url} is missing openapi 3.x / info / paths",
                            rule_id="openapi-invalid",
                        )
                    ],
                )
            continue
        return CheckOutcome(
            "openapi-conformance",
            STATUS_PASS,
            f"Server publishes a valid OpenAPI {doc['openapi']} contract at {url}.",
        )

    # 2. Fall back to MCP-native equivalent (tools/list with inputSchema).
    if _has_mcp_native_contract(tools_resp):
        return CheckOutcome(
            "openapi-conformance",
            STATUS_PASS,
            "No REST OpenAPI contract, but server publishes an MCP-native equivalent: "
            "tools/list with well-formed inputSchema per tool (ADR: 'OpenAPI or equivalent').",
        )

    # 3. Neither.
    return CheckOutcome(
        "openapi-conformance",
        STATUS_FAIL,
        "No API contract found: no REST OpenAPI document and no MCP-native tools/list with schemas.",
        findings=[
            make_finding(
                severity="high",
                message="No discoverable API contract (REST OpenAPI or MCP-native tools/list with inputSchema)",
                rule_id="openapi-missing",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def probe_all(server_url: str, *, timeout: float = 30.0, openapi_url: str | None = None) -> list[CheckOutcome]:
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
            # Fetch page one first so a broken tools/list endpoint is visible to the
            # checks. If it is well-formed, aggregate every page (follows nextCursor)
            # so schema/annotation checks see tools beyond page one, wrapped in a
            # synthetic response so the per-check helpers keep one interface. If it is
            # malformed/errored, pass the real response through so schema-conformance
            # fails instead of a synthetic empty wrapper reading as not_evaluated.
            first_page = client.list_tools()
            if _tools_list_error(first_page) is None:
                # Extract page-1 tools and cursor. If there's a next page, resume
                # pagination from there (avoids re-fetching page 1).
                fp_result = first_page.message.get("result", {}) if isinstance(first_page.message, dict) else {}
                fp_tools = (
                    [t for t in fp_result.get("tools", []) if isinstance(t, dict)]
                    if isinstance(fp_result, dict)
                    else []
                )
                next_cursor = fp_result.get("nextCursor") if isinstance(fp_result, dict) else None
                if next_cursor and isinstance(next_cursor, str):
                    all_tools = client.list_all_tools(
                        initial_tools=fp_tools,
                        start_cursor=next_cursor,
                    )
                    tools_resp = RpcResponse(
                        status_code=200,
                        headers={},
                        content_type="application/json",
                        message={"jsonrpc": "2.0", "id": 2, "result": {"tools": all_tools}},
                        raw_text="",
                    )
                else:
                    tools_resp = first_page
            else:
                tools_resp = first_page
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
            check_openapi_conformance(client, tools_resp, openapi_url=openapi_url),
        ]
