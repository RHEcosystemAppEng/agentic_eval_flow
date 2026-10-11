# `scripts/mcp/` — MCP evaluation execution layer

This package is the **execution / I/O layer** for the MCP-server evaluation
pipeline. These modules shell out to external tools (gitleaks, semgrep,
licensee), probe the live MCP server over HTTP, and read/write report JSON. The
pure pass/fail evaluation logic they feed lives in `abevalflow.mcp`.

## Naming convention

Role is encoded in the filename prefix:

| Prefix | Role | Example |
|--------|------|---------|
| `run_` | Entry point — invoked by a Tekton task as `python -m scripts.mcp.<name>` (or via `run_gate.sh`). | `run_secrets_scan.py` |
| `_`    | Helper — imported by the entry points, never run directly. | `_common.py` |

## Entry points

| Module | Phase | Invoked by | Emits |
|--------|-------|------------|-------|
| `run_secrets_scan.py`       | 1 | phase1 task | `secrets-scan.json` (gitleaks) |
| `run_no_user_code_scan.py`  | 1 | phase1 task | `no-user-code-scan.json` (semgrep, bundled offline ruleset) |
| `run_license_scan.py`       | 1 | phase1 task | `license-scan.json` (licensee, top-level license only) |
| `run_phase1_gates.py`       | 1 | phase1 task (via `run_gate.sh`) | `phase1-summary.json` + gate exit code |
| `run_phase2_probe.py`       | 2 | phase2 task | per-check result files (live conformance probe) |
| `run_compass_fetch.py`      | 2 | phase2 task | per-check result files (Compass tier-1 facts) |
| `run_phase2_gates.py`       | 2 | phase2 task (via `run_gate.sh`) | `phase2-summary.json` + gate exit code |

`run_gate.sh` is the shared gate wrapper baked into the eval image: both phase
tasks call it to run the phase's gate module, emit the Tekton results
(`passed` flag + report path) from the summary JSON, and propagate the gate's
exit code.

## Helpers

| Module | Used by |
|--------|---------|
| `_common.py`     | Phase 1 scanners — findings, tool runner, report writer |
| `_phase2.py`     | Phase 2 probe + Compass fetch — check result shapes |
| `_mcp_client.py` | Phase 2 probe — minimal MCP client over Streamable HTTP |

## Layout

The package is intentionally flat, matching the repo-wide `scripts/`
convention. The `run_`/`_` prefixes keep entry points and helpers legible
without subpackages.
