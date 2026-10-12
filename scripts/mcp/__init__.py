"""Thin CLI runners for the MCP evaluation pipeline.

These entry points are the **public API of the eval container image**: each is
invoked by a Tekton task step as ``python -m scripts.mcp.<name>``. They shell out
to external tools, create an MCP client and call evaluation logic from
``abevalflow.mcp``, or read/write report JSON. All substantial evaluation logic
lives in ``abevalflow.mcp`` (types, client, gates, checks, Compass mapping);
these runners are intentionally thin wrappers around it.

Entry points (``run_``-prefixed):

- ``run_secrets_scan``        - Phase 1 secrets scan (gitleaks)
- ``run_no_user_code_scan``   - Phase 1 no-user-code scan (semgrep)
- ``run_license_scan``        - Phase 1 license scan (licensee)
- ``run_phase1_gates``        - Phase 1 gate evaluation + summary
- ``run_phase2_probe``        - Phase 2 live conformance probe
- ``run_compass_fetch``       - Phase 2 Compass-fact consume
- ``run_phase2_gates``        - Phase 2 gate evaluation + summary

Scanner I/O helpers (underscore-prefixed, never run directly):

- ``_common``                 - ``run_tool``, ``write_scan``
"""
