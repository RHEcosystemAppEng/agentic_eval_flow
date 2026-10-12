# `scripts/mcp/` — MCP evaluation pipeline runners

Thin CLI entry points for the MCP-server evaluation pipeline. These are the
**public API of the eval container image**: each is invoked by a Tekton task
step as `python -m scripts.mcp.<name>`. They shell out to external tools,
create an MCP client, read files, and write result JSON. All substantial
evaluation logic lives in `abevalflow.mcp`.

## Two-package split

| Package | Role | Contains |
|---------|------|----------|
| `abevalflow/mcp/` | **What** to decide | Gates, checks, types, client, Compass mapping — no I/O to external tools |
| `scripts/mcp/` (here) | **How** to collect data | CLI entry points that shell out to tools, invoke the logic, write results |

The contract between them: **JSON files on disk.** Scanners/probe write them.
Gates read them.

## Entry points

| Module | Phase | What it does | Emits |
|--------|-------|-------------|-------|
| `run_secrets_scan.py`       | 1 | Shells out to gitleaks | `secrets-scan.json` |
| `run_no_user_code_scan.py`  | 1 | Shells out to semgrep (bundled offline ruleset) | `no-user-code-scan.json` |
| `run_license_scan.py`       | 1 | Shells out to licensee (top-level license only) | `license-scan.json` |
| `run_phase1_gates.py`       | 1 | Calls `abevalflow.mcp.phase1.run_phase1()` | `phase1-summary.json` + gate exit code |
| `run_phase2_probe.py`       | 2 | Creates MCPClient, calls `probe_all()` | per-check `<name>-check.json` files |
| `run_compass_fetch.py`      | 2 | Reads Compass facts file, calls `map_facts()` | per-check `<name>-check.json` files |
| `run_phase2_gates.py`       | 2 | Calls `abevalflow.mcp.phase2.run_phase2()` | `phase2-summary.json` + gate exit code |

`run_gate.sh` is the shared bash gate wrapper baked into the eval image.

## Helpers

| Module | What it provides |
|--------|-----------------|
| `_common.py` | `run_tool()` (subprocess runner), `write_scan()` (scan JSON writer) — scanner I/O only |

## Naming convention

- `run_` prefix = Tekton-invoked entry point (`python -m scripts.mcp.<name>`)
- `_` prefix = imported helper, never run directly

## Future repo split

If Tekton task YAMLs move to a separate repo, this package stays in the image
repo (baked into the eval container). The new repo calls these entry points via
`python -m` — no Python imports cross the repo boundary.
