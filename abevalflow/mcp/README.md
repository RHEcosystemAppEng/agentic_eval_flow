# `abevalflow/mcp/` — MCP evaluation pipeline logic

All evaluation logic for the MCP-server evaluation pipeline (ADR Approach 4,
Phases 1 and 2). This package decides pass/fail — it reads result JSON, applies
policy, and produces gate verdicts. No shell-outs to external tools, no network
calls. Testable with JSON fixtures.

## Structure

| Module | What it does |
|--------|-------------|
| `types.py` | Shared types: `CheckOutcome`, `STATUS_*` (three-state), `make_finding`, `write_check_result` |
| `client.py` | `MCPClient` — JSON-RPC 2.0 over Streamable HTTP, purpose-built for conformance probing |

### Phase 1 — static / build-time gates

| Module | Class | Reads |
|--------|-------|-------|
| `phase1/secrets_gate.py` | `SecretsGate` | `secrets-scan.json` (gitleaks) |
| `phase1/no_user_code_gate.py` | `NoUserCodeGate` | `no-user-code-scan.json` (semgrep) |
| `phase1/license_gate.py` | `LicenseGate` | `license-scan.json` (licensee) |

Orchestrated by `phase1/__init__.py` → `run_phase1()`.

### Phase 2 — deterministic conformance

| Module | What it does |
|--------|-------------|
| `phase2/gate.py` | `Phase2Gate` — reads any `<check>-check.json`, maps three-state result to `GateResult` |
| `phase2/checks.py` | 8 `check_*` functions + `probe_all()` — conformance checks against a running MCP server |
| `phase2/compass.py` | `map_facts()` — maps Compass SoundCheck facts to `CheckOutcome` (tool-name, OAuth) |

Orchestrated by `phase2/__init__.py` → `run_phase2()`.

## Data flow

```
Phase 1 (static — no running server)
┌─────────────────────────────────────────────────────────────┐
│ scripts/mcp/                                                │
│   run_secrets_scan       ──→ secrets-scan.json               │
│   run_no_user_code_scan  ──→ no-user-code-scan.json          │
│   run_license_scan       ──→ license-scan.json               │
└──────────────────────────────┬──────────────────────────────┘
                               │ JSON on disk
┌──────────────────────────────▼──────────────────────────────┐
│ abevalflow/mcp/phase1/                                      │
│   SecretsGate       ──→ GateResult (pass/fail)               │
│   NoUserCodeGate    ──→ GateResult (pass/fail)               │
│   LicenseGate       ──→ GateResult (pass/fail)               │
└─────────────────────────────────────────────────────────────┘

Phase 2 (live — against the running MCP server)
┌─────────────────────────────────────────────────────────────┐
│ scripts/mcp/                                                │
│   run_phase2_probe  ──→ <check>-check.json (per check)       │
│   run_compass_fetch ──→ <check>-check.json (consumed facts)  │
└──────────────────────────────┬──────────────────────────────┘
                               │ JSON on disk
┌──────────────────────────────▼──────────────────────────────┐
│ abevalflow/mcp/phase2/                                      │
│   Phase2Gate (×N)   ──→ GateResult (pass/fail/not_evaluated) │
└─────────────────────────────────────────────────────────────┘
```

## Relationship to `scripts/mcp/`

This package contains the **what** (evaluation logic). `scripts/mcp/` contains
the **how** (CLI entry points that shell out to tools, create the client, read
files, and write results). The contract between them is JSON files on disk.
