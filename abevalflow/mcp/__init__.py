"""MCP server evaluation pipeline — evaluation logic.

All evaluation logic for the MCP-server evaluation pipeline lives here: gates,
check functions, data types, the MCP protocol client, and Compass fact mapping.
Isolated from the skill evaluation pipeline (see ADR: Evaluation Strategy for
MCP Servers as Standalone Software Components, Approach 4).

Layout (organized by role and evaluation type):

- ``types``       - core types: ``CheckOutcome``, ``STATUS_*``
- ``helpers``     - finding builders, severity normalization, check-result I/O
- ``mcp_client``  - MCP protocol client (JSON-RPC over Streamable HTTP)
- ``gates/``      - all pass/fail gates (static + conformance evaluation)
- ``checks/``     - conformance check functions + Compass mapping

The CLI entry points that invoke this logic and handle I/O (shell out to
scanners, create an MCP client, read/write files) live in ``scripts/mcp/``.

Static and conformance evaluations are implemented here; behavioral evaluation
(mcpchecker) is out of scope for now.
"""
