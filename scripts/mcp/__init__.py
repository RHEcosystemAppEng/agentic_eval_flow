"""Execution / I/O layer for the MCP evaluation pipeline.

These modules shell out to external tools, probe the live server, and read/write
report JSON. The pure pass/fail evaluation logic they feed lives in
``abevalflow.mcp``.

Tekton entry points (run as ``python -m scripts.mcp.<name>``):

- ``secrets_scan``       - Phase 1 secrets scan (gitleaks)
- ``no_user_code_scan``  - Phase 1 no-user-code scan (semgrep)
- ``license_scan``       - Phase 1 license scan (licensee)
- ``run_phase1_gates``   - Phase 1 gate evaluation + summary
- ``phase2_probe``       - Phase 2 live conformance probe
- ``compass_fetch``      - Phase 2 Compass-fact consume
- ``run_phase2_gates``   - Phase 2 gate evaluation + summary

Internal helpers (imported by the entry points, never run directly) are
underscore-prefixed: ``_common``, ``_phase2``, ``_mcp_client``.
"""
