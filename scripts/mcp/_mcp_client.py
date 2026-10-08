"""Minimal MCP client over Streamable HTTP for Phase 2 conformance probing.

Hand-rolled JSON-RPC 2.0 over ``httpx`` (deliberately NOT the ``mcp`` SDK) so the
Phase 2 probe can:

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
CLIENT_INFO = {"name": "mcp-phase2-probe", "version": "0.1"}


@dataclass
class RpcResponse:
    """Raw result of a single POST to the MCP endpoint.

    ``message`` is the parsed JSON-RPC object (from a plain JSON body or scraped
    from the first SSE ``data:`` frame), or ``None`` when the body is empty (e.g.
    a 202 to a notification) or unparseable.
    """

    status_code: int
    headers: dict[str, str]
    content_type: str
    message: dict[str, Any] | list[Any] | None
    raw_text: str

    @property
    def is_sse(self) -> bool:
        return "text/event-stream" in self.content_type


def _parse_sse(text: str) -> dict[str, Any] | list[Any] | None:
    """Extract the first JSON-RPC message from an SSE stream body."""
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = line[len("data:") :].strip()
            if not payload:
                continue
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                continue
    return None


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
        try:
            resp = self._client.post(self.endpoint_url, content=content, headers=headers)
        except httpx.HTTPError as exc:
            raise MCPConnectionError(str(exc)) from exc

        text = resp.text
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            message = _parse_sse(text)
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

    def list_tools(self, *, request_id: Any = 2) -> RpcResponse:
        return self.post({"jsonrpc": "2.0", "id": request_id, "method": "tools/list", "params": {}})

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
