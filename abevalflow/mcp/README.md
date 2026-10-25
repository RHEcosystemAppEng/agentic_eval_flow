# `abevalflow/mcp/` — MCP evaluation pipeline logic

All evaluation logic for the MCP-server evaluation pipeline (ADR Approach 4:
static, conformance, and behavioral evaluations). This package **reads** result
JSON, applies policy, and **decides** pass/fail. No shell-outs to external tools,
no network calls. Testable with JSON fixtures.

## Structure (organized by role and evaluation type)

| Directory | Role | What it does |
|-----------|------|-------------|
| `gates/` | **Decide** pass/fail | Read result JSON, count findings, apply policy, produce `GateResult`. One gate per check type. |
| `checks/` | **Define** check logic | The conformance check functions (what to probe, how to evaluate) and Compass fact/result mapping. Called by the probe runners in `scripts/mcp/probes/`. |

### `gates/` — all pass/fail gates

| File | Evaluation Type | What it does |
|------|-----------------|-------------|
| `static_eval_secrets_gate.py` | Static | `SecretsGate` — reads `secrets-scan.json`, counts HIGH findings |
| `static_eval_no_user_code_gate.py` | Static | `NoUserCodeGate` — reads `no-user-code-scan.json`, counts HIGH findings |
| `static_eval_license_gate.py` | Static | `LicenseGate` — reads `license-scan.json`, checks against allow-list |
| `conformance_eval_gate.py` | Conformance | `ConformanceEvalGate` — reads any `<check>-check.json`, maps three-state to `GateResult` |
| `__init__.py` | All | Orchestrators: `run_static_eval()`, `run_conformance_eval()`, gate lists |

### `checks/` — conformance probes and Compass-consumed checks

| File | Evaluation Type | What it does |
|------|-----------------|-------------|
| `conformance_eval_checks.py` | Conformance | 8 `check_*` functions + `probe_all()` — conformance checks against a running MCP server |
| `conformance_eval_compass_checks.py` | Conformance | `map_facts()` + `map_results()` — maps Compass facts/results to `CheckOutcome` |

### Root modules

| File | What it does |
|------|-------------|
| `types.py` | Core types: `CheckOutcome`, `STATUS_PASS/FAIL/NOT_EVALUATED` |
| `helpers.py` | `make_finding()`, `normalize_severity()`, `write_check_result()` |
| `mcp_client.py` | `MCPClient` — JSON-RPC 2.0 over Streamable HTTP, purpose-built for conformance probing |

## Data flow

```
Static Evaluation (build-time — no running server)
┌─────────────────────────────────────────────────────────────────┐
│ scripts/mcp/scanners/                                           │
│   static_eval_secrets_scan       ──→ secrets-scan.json          │
│   static_eval_no_user_code_scan  ──→ no-user-code-scan.json     │
│   static_eval_license_scan       ──→ license-scan.json          │
└──────────────────────────────────┬──────────────────────────────┘
                                   │ JSON on disk
┌──────────────────────────────────▼──────────────────────────────┐
│ abevalflow/mcp/gates/                                           │
│   SecretsGate           ──→ GateResult (pass/fail)              │
│   NoUserCodeGate        ──→ GateResult (pass/fail)              │
│   LicenseGate           ──→ GateResult (pass/fail)              │
└─────────────────────────────────────────────────────────────────┘

Conformance Evaluation (live — against the running MCP server)
┌─────────────────────────────────────────────────────────────────┐
│ scripts/mcp/probes/                                             │
│   conformance_eval_probe   ──→ <check>-check.json (per check)  │
│   conformance_eval_compass_probe ──→ <check>-check.json (facts)│
└──────────────────────────────────┬──────────────────────────────┘
                                   │ JSON on disk
┌──────────────────────────────────▼──────────────────────────────┐
│ abevalflow/mcp/gates/                                           │
│   ConformanceEvalGate (×N)  ──→ GateResult (pass/fail/not_eval) │
└─────────────────────────────────────────────────────────────────┘
```

## Relationship to `scripts/mcp/`

This package contains the **what** (evaluation logic — reads JSON, decides).
`scripts/mcp/` contains the **how** (CLI entry points — runs tools, writes JSON).
The contract between them is JSON files on disk.

## Three-state model (Conformance Evaluation)

Every conformance evaluation check produces one of three states:

| State | Meaning | Effect on verdict | Effect on grade |
|---|---|---|---|
| `pass` | Server is conformant | Does not block | Score 1.0 |
| `fail` | Server violates requirement | **Blocks** (in block mode) | Score weighted by severity |
| `not_evaluated` | Check could not produce a signal | Does not block | **Excluded** from grade |

A server is never penalized for something it was never tested for.

## Adding a new conformance check

1. **Add the check function** in `checks/conformance_eval_checks.py` — takes the
   client and/or a response, returns a `CheckOutcome`.
2. **Wire it** in `probe_all()` (same file).
3. **Add the check name** to `CONFORMANCE_EVAL_CHECKS` in `gates/__init__.py`.
4. The gate picks it up automatically — it reads any `<check>-check.json` file.
   No gate code changes needed.

## Testing

All code in this package is testable with JSON fixtures — no running server,
no external tools. Tests are in `tests/test_mcp_static_eval.py` and
`tests/test_mcp_conformance_eval.py`.
