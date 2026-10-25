"""Minimal MCP client over Streamable HTTP for conformance evaluation probing.

Hand-rolled JSON-RPC 2.0 over ``httpx`` (deliberately NOT the ``mcp`` SDK) so the
conformance evaluation probe can:

* send well-formed calls (initialize, tools/list, tools/call), and
* send deliberately malformed / spec-violating payloads and inspect the raw
  HTTP response (status code, headers, content-type, body).

The typed SDK client cannot emit malformed frames and raises on non-conformant
server responses instead of letting us assert on them, which is exactly what the
conformance checks need to observe. ``httpx`` is already a project dependency.

Streamable HTTP handshake (MCP spec, transport rev 2025-06-18):
  1. POST ``initialize`` with ``Accept: application/json, text/event-stream``.
  2. Capture ``Mcp-Session-Id`` from the response headers; echo it on every
     later request.
  3. POST the ``notifications/initialized`` notification (no id) -> 202, no body.
  4. Include ``MCP-Protocol-Version`` on post-init requests.
  5. A request may be answered with ``application/json`` OR ``text/event-stream``
     (SSE); both are handled here.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_PROTOCOL_VERSION = "2025-06-18"
ACCEPT_HEADER = "application/json, text/event-stream"
CLIENT_INFO = {"name": "mcp-conformance-eval-probe", "version": "0.1"}


@dataclass
class RpcResponse:
    """Raw result of a single POST to the MCP endpoint.

    ``message`` is the parsed JSON-RPC object (from a plain JSON body, or the SSE
    ``data:`` frame whose id matches the request), or ``None`` when the body is
    empty (e.g. a 202 to a notification) or unparseable.
    """

    status_code: int
    headers: dict[str, str]
    content_type: str
    message: dict[str, Any] | list[Any] | None
    raw_text: str

    @property
    def is_sse(self) -> bool:
        return "text/event-stream" in self.content_type


def _parse_sse_messages(text: str) -> list[dict[str, Any] | list[Any]]:
    """Extract every JSON-RPC message from an SSE stream body, in order."""
    messages: list[dict[str, Any] | list[Any]] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            if not payload:
                continue
            try:
                messages.append(json.loads(payload))
            except json.JSONDecodeError:
                continue
    return messages


def _select_response(messages: list[dict[str, Any] | list[Any]], request_id: Any) -> dict[str, Any] | list[Any] | None:
    """Pick the JSON-RPC response matching ``request_id``.

    A server may interleave notifications (no/other id) ahead of the response on
    the stream, so selecting the first frame can mis-read a valid response as a
    protocol violation. Matching is tried exactly first, then by string value (a
    server that echoes the id as "2" rather than 2 still matches). When
    ``request_id`` is None (e.g. a notification or a raw malformed frame) or no
    frame matches, fall back to the first message.
    """
    if request_id is not None:
        for msg in messages:
            if isinstance(msg, dict) and msg.get("id") == request_id:
                return msg
        for msg in messages:
            if isinstance(msg, dict) and "id" in msg and str(msg.get("id")) == str(request_id):
                return msg
    return messages[0] if messages else None


class MCPConnectionError(RuntimeError):
    """Raised when the server cannot be reached at all (used to mark checks not-evaluated)."""


class MCPClient:
    """Small stateful client for one MCP server endpoint."""

    def __init__(
        self,
        endpoint_url: str,
        *,
        timeout: float = 30.0,
        protocol_version: str = DEFAULT_PROTOCOL_VERSION,
    ) -> None:
        self.endpoint_url = endpoint_url
        self.protocol_version = protocol_version
        self.negotiated_version: str | None = None
        self.session_id: str | None = None
        self._client = httpx.Client(timeout=timeout)

    # -- context management -------------------------------------------------
    def __enter__(self) -> MCPClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- low-level POST -----------------------------------------------------
    def post(
        self,
        body: Any,
        *,
        include_session: bool = True,
        include_version: bool = True,
        extra_headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> RpcResponse:
        """POST a payload and return the raw parsed response.

        ``body`` is JSON-encoded unless ``raw=True``, in which case it is sent
        verbatim (used to send malformed frames for negative conformance tests).
        Raises :class:`MCPConnectionError` only when the server is unreachable.
        """
        headers = {"Accept": ACCEPT_HEADER, "Content-Type": "application/json"}
        if include_version:
            headers["MCP-Protocol-Version"] = self.negotiated_version or self.protocol_version
        if include_session and self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if extra_headers:
            headers.update(extra_headers)

        content = body if raw else json.dumps(body)
        request_id = body.get("id") if isinstance(body, dict) else None
        try:
            resp = self._client.post(self.endpoint_url, content=content, headers=headers)
        except httpx.HTTPError as exc:
            raise MCPConnectionError(str(exc)) from exc

        text = resp.text
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            message = _select_response(_parse_sse_messages(text), request_id)
        elif text.strip():
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                message = None
        else:
            message = None

        return RpcResponse(
            status_code=resp.status_code,
            headers=dict(resp.headers),
            content_type=content_type,
            message=message,
            raw_text=text,
        )

    # -- plain GET (non-JSON-RPC, e.g. fetching an OpenAPI contract) ---------
    def get(self, url: str, *, extra_headers: dict[str, str] | None = None) -> tuple[int, str, str]:
        """GET ``url`` and return ``(status_code, content_type, text)``.

        A plain HTTP GET, not a JSON-RPC call - used to fetch side artifacts such
        as an OpenAPI contract. Raises :class:`MCPConnectionError` when the host
        cannot be reached at all; a non-2xx status is returned normally so the
        caller can decide what it means.
        """
        headers = {"Accept": "application/json, application/yaml, text/yaml, */*"}
        if extra_headers:
            headers.update(extra_headers)
        try:
            resp = self._client.get(url, headers=headers, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise MCPConnectionError(str(exc)) from exc
        return resp.status_code, resp.headers.get("content-type", ""), resp.text

    # -- MCP protocol helpers ----------------------------------------------
    def initialize(self, *, request_id: Any = 1, params: dict[str, Any] | None = None) -> RpcResponse:
        """Send the ``initialize`` request and capture session id + negotiated version."""
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "initialize",
            "params": params
            or {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        }
        # No Mcp-Session-Id yet, and MCP-Protocol-Version is a post-init header
        # (the version is negotiated via the params body here).
        resp = self.post(body, include_session=False, include_version=False)
        # Session id lives in the response headers (case-insensitive).
        for key, value in resp.headers.items():
            if key.lower() == "mcp-session-id":
                self.session_id = value
                break
        if isinstance(resp.message, dict):
            result = resp.message.get("result", {})
            if isinstance(result, dict) and result.get("protocolVersion"):
                self.negotiated_version = result["protocolVersion"]
        return resp

    def send_initialized(self) -> RpcResponse:
        """Send the ``notifications/initialized`` notification (expects 202, empty body)."""
        return self.post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self, *, request_id: Any = 2, cursor: str | None = None) -> RpcResponse:
        params: dict[str, Any] = {"cursor": cursor} if cursor else {}
        return self.post({"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": params})

    def list_all_tools(self, *, max_pages: int = 50) -> list[dict[str, Any]]:
        """Return every tool across all ``tools/list`` pages, following ``nextCursor``.

        Paginating matters for conformance: a server can place a malformed tool on
        a later page, so validating only page one would miss it. ``max_pages``
        bounds a server that returns an endless or self-repeating cursor.
        """
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for page in range(max_pages):
            resp = self.list_tools(request_id=2 + page, cursor=cursor)
            result = resp.message.get("result") if isinstance(resp.message, dict) else None
            if not isinstance(result, dict):
                break
            page_tools = result.get("tools", [])
            if isinstance(page_tools, list):
                tools.extend(t for t in page_tools if isinstance(t, dict))
            cursor = result.get("nextCursor")
            if not cursor or not isinstance(cursor, str) or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None, *, request_id: Any = 3) -> RpcResponse:
        return self.post(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )

    def handshake(self) -> RpcResponse:
        """Convenience: initialize then send the initialized notification."""
        resp = self.initialize()
        self.send_initialized()
        return resp
