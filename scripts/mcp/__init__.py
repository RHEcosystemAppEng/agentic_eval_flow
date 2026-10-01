"""Scanner scripts for the MCP Phase 1 static / build-time checks.

Each module runs an external, language-agnostic static-analysis tool against an
MCP server repository and normalizes its output into the shared
``{"findings": [...]}`` schema that ``abevalflow.mcp.phase1`` gates read.
"""
