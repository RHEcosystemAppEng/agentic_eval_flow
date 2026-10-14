# `abevalflow/mcp/` — MCP evaluation pipeline logic

All evaluation logic for the MCP-server evaluation pipeline (ADR Approach 4,
Phases 1 and 2). This package **reads** result JSON, applies policy, and
**decides** pass/fail. No shell-outs to external tools, no network calls.
Testable with JSON fixtures.

## Structure (organized by role, phase-prefixed)

| Directory | Role | What it does |
|-----------|------|-------------|
| `gates/` | **Decide** pass/fail | Read result JSON, count findings, apply policy, produce `GateResult`. One gate per check type. |
| `checks/` | **Define** check logic | The conformance check functions (what to probe, how to evaluate) and Compass fact/result mapping. Called by the probe runners in `scripts/mcp/probes/`. |

### `gates/` — all pass/fail gates

| File | Phase | What it does |
|------|-------|-------------|
| `phase1_secrets_gate.py` | 1 | `SecretsGate` — reads `secrets-scan.json`, counts HIGH findings |
| `phase1_no_user_code_gate.py` | 1 | `NoUserCodeGate` — reads `no-user-code-scan.json`, counts HIGH findings |
| `phase1_license_gate.py` | 1 | `LicenseGate` — reads `license-scan.json`, checks against allow-list |
| `phase2_conformance_gate.py` | 2 | `Phase2Gate` — reads any `<check>-check.json`, maps three-state to `GateResult` |
| `__init__.py` | 1+2 | Orchestrators: `run_phase1()`, `run_phase2()`, gate lists |

### `checks/` — conformance probes and Compass-consumed checks

| File | Phase | What it does |
|------|-------|-------------|
| `phase2_conformance_checks.py` | 2 | 8 `check_*` functions + `probe_all()` — conformance checks against a running MCP server |
| `phase2_compass_checks.py` | 2 | `map_facts()` + `map_results()` — maps Compass facts/results to `CheckOutcome` |

### Root modules

| File | What it does |
|------|-------------|
| `types.py` | Core types: `CheckOutcome`, `STATUS_PASS/FAIL/NOT_EVALUATED` |
| `helpers.py` | `make_finding()`, `normalize_severity()`, `write_check_result()` |
| `mcp_client.py` | `MCPClient` — JSON-RPC 2.0 over Streamable HTTP, purpose-built for conformance probing |

## Data flow

```
Phase 1 (static — no running server)
┌─────────────────────────────────────────────────────────────────┐
│ scripts/mcp/scanners/                                           │
│   phase1_secrets_scan       ──→ secrets-scan.json                │
│   phase1_no_user_code_scan  ──→ no-user-code-scan.json           │
│   phase1_license_scan       ──→ license-scan.json                │
└──────────────────────────────────┬──────────────────────────────┘
                                   │ JSON on disk
┌──────────────────────────────────▼──────────────────────────────┐
│ abevalflow/mcp/gates/                                           │
│   Phase1SecretsGate     ──→ GateResult (pass/fail)               │
│   Phase1NoUserCodeGate  ──→ GateResult (pass/fail)               │
│   Phase1LicenseGate     ──→ GateResult (pass/fail)               │
└─────────────────────────────────────────────────────────────────┘

Phase 2 (live — against the running MCP server)
┌─────────────────────────────────────────────────────────────────┐
│ scripts/mcp/probes/                                             │
│   phase2_conformance_probe   ──→ <check>-check.json (per check)       │
│   phase2_compass_probe ──→ <check>-check.json (consumed facts)  │
└──────────────────────────────────┬──────────────────────────────┘
                                   │ JSON on disk
┌──────────────────────────────────▼──────────────────────────────┐
│ abevalflow/mcp/gates/                                           │
│   Phase2ConformanceGate (×N)  ──→ GateResult (pass/fail/not_evaluated) │
└─────────────────────────────────────────────────────────────────┘
```

## Relationship to `scripts/mcp/`

This package contains the **what** (evaluation logic — reads JSON, decides).
`scripts/mcp/` contains the **how** (CLI entry points — runs tools, writes JSON).
The contract between them is JSON files on disk.

## Three-state model (Phase 2)

Every Phase 2 check produces one of three states:

| State | Meaning | Effect on verdict | Effect on grade |
|---|---|---|---|
| `pass` | Server is conformant | Does not block | Score 1.0 |
| `fail` | Server violates requirement | **Blocks** (in block mode) | Score weighted by severity |
| `not_evaluated` | Check could not produce a signal | Does not block | **Excluded** from grade |

A server is never penalized for something it was never tested for.

## Adding a new check

1. **Add the check function** in `checks/phase2_conformance_checks.py` — takes the
   client and/or a response, returns a `CheckOutcome`.
2. **Wire it** in `probe_all()` (same file).
3. **Add the check name** to `PHASE2_CHECKS` in `gates/__init__.py`.
4. The gate picks it up automatically — it reads any `<check>-check.json` file.
   No gate code changes needed.

## Testing

All code in this package is testable with JSON fixtures — no running server,
no external tools. Tests are in `tests/test_mcp_phase1.py` and
`tests/test_mcp_phase2.py`.
