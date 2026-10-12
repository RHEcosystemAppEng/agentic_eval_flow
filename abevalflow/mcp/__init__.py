"""MCP server evaluation library.

All evaluation logic for the MCP-server evaluation pipeline lives here: gates,
check functions, data types, the MCP protocol client, and Compass fact mapping.
Isolated from the skill evaluation pipeline (see ADR: Evaluation Strategy for
MCP Servers as Standalone Software Components, Approach 4).

Layout:

- ``types``    - shared types: ``CheckOutcome``, ``STATUS_*``, ``make_finding``
- ``client``   - MCP protocol client (JSON-RPC over Streamable HTTP)
- ``phase1/``  - Phase 1 gates (secrets, no-user-code, license)
- ``phase2/``  - Phase 2 gate + conformance check functions + Compass mapping

The CLI entry points that invoke this library and handle I/O (shell out to
scanners, create an MCP client, read/write files) live in ``scripts/mcp/``.

Phases 1 and 2 are implemented here; Phase 3 (behavioral, mcpchecker) is out of
scope for now.
"""
