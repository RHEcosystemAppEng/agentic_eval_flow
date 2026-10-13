"""Thin CLI runners for the MCP evaluation pipeline.

These entry points are the **public API of the eval container image**: each is
invoked by a Tekton task step as ``python -m scripts.mcp.<subpackage>.<name>``.
They shell out to external tools, create an MCP client and call evaluation logic
from ``abevalflow.mcp``, or read/write report JSON. All substantial evaluation
logic lives in ``abevalflow.mcp``; these runners are intentionally thin wrappers.

Organized by role:

- ``scanners/``  - Phase 1 scanner runners (gitleaks, semgrep, licensee)
- ``gates/``     - Gate evaluation runners + gate_wrapper.sh
- ``probes/``    - Phase 2 probe + Compass consume runners
- ``rules/``     - Bundled offline rulesets (semgrep)
"""
