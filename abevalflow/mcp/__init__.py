"""MCP server evaluation pipeline.

Isolated from the skill evaluation pipeline (see ADR: Evaluation Strategy for
MCP Servers as Standalone Software Components, Approach 4). Evaluates an MCP
server as a black box across three sequenced phases:

- Phase 1 - static / build-time analysis (no running server).
- Phase 2 - deterministic contract/conformance testing (live, no LLM).
- Phase 3 - behavioral testing (mcpchecker, conditional on a task suite).

Only Phase 1 is implemented here so far.
"""
