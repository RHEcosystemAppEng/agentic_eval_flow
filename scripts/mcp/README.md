# `scripts/mcp/` — MCP evaluation pipeline runners

Thin CLI entry points for the MCP-server evaluation pipeline. These are the
**public API of the eval container image**: each is invoked by a Tekton task
step as `python -m scripts.mcp.<subpackage>.<name>`. They shell out to external
tools, invoke evaluation logic from `abevalflow.mcp`, and write result JSON.

## Structure (organized by role and evaluation type)

| Directory | Role | What it does |
|-----------|------|-------------|
| `scanners/` | **Scan** source code | Shell out to external tools (gitleaks, semgrep, licensee) and write normalized scan JSON. Static evaluation only — no running server needed. |
| `runners/` | **Run** gate evaluations | Thin CLIs that invoke the gate logic from `abevalflow.mcp.gates` and write summary JSON. All evaluation types. |
| `probes/` | **Probe** the live server | Connect to the running MCP server (or Compass API) and write per-check result JSON. Conformance evaluation only — server must be running. |
| `rules/` | **Bundled rulesets** | Offline semgrep rules for the no-user-code scan. Not a runner — consumed by the scanner. |

### `scanners/` — Static evaluation scanner runners

| File | What it does | Emits |
|------|-------------|-------|
| `static_eval_secrets_scan.py` | Shells out to gitleaks | `secrets-scan.json` |
| `static_eval_no_user_code_scan.py` | Shells out to semgrep (bundled offline ruleset) | `no-user-code-scan.json` |
| `static_eval_license_scan.py` | Shells out to licensee (top-level license only) | `license-scan.json` |
| `helpers.py` | `run_tool()` + `write_scan()` — scanner I/O helpers | — |

### `runners/` — gate evaluation runners

| File | What it does | Emits |
|------|-------------|-------|
| `static_eval_gate.py` | Calls `abevalflow.mcp.gates.run_static_eval()` | `static-eval-summary.json` |
| `conformance_eval_gate.py` | Calls `abevalflow.mcp.gates.run_conformance_eval()` | `conformance-eval-summary.json` |
| `behavioral_eval_summary.py` | Maps mcpchecker results to unified summary | `behavioral-eval-summary.json` |

### `gate_wrapper.sh` — shared Tekton gate wrapper

Lives at `scripts/mcp/gate_wrapper.sh` (flat, following repo convention for
utility scripts). Both Tekton gate steps call it to run the gate module, emit
Tekton results (`passed` flag + report path), and propagate the exit code.

### `probes/` — Conformance evaluation probe runners

| File | What it does | Emits |
|------|-------------|-------|
| `conformance_eval_probe.py` | Creates MCPClient, calls `probe_all()` | per-check `<name>-check.json` |
| `conformance_eval_compass_probe.py` | Fetches Compass results (live API or file mode) | per-check `<name>-check.json` |

### `rules/` — bundled offline rulesets

| File | Used by |
|------|---------|
| `no_user_code.yml` | semgrep (static evaluation no-user-code scan) |

## Two-package split

| Package | Role | Contains |
|---------|------|----------|
| `abevalflow/mcp/` | **What** to decide | Gates, checks, types, client, Compass mapping — reads JSON, decides pass/fail |
| `scripts/mcp/` (here) | **How** to collect data | CLI entry points that run tools, write results — the public API of the eval image |

The contract between them: **JSON files on disk.** Writers produce them, readers consume them.

## Running locally

`run_mcp_eval.sh` is the local dev/testing runner. It mirrors what the Tekton
Tasks do, so a local run exercises the same code paths as a cluster run.

```bash
# Both evaluations — scan source + probe running server:
bash scripts/mcp/run_mcp_eval.sh \
    --repo /path/to/mcp-server-source \
    --server-url http://localhost:8080/mcp

# Static evaluation only (no server needed):
bash scripts/mcp/run_mcp_eval.sh --repo /path/to/mcp-server-source

# Conformance evaluation only (server must be running):
bash scripts/mcp/run_mcp_eval.sh --server-url http://localhost:8080/mcp

# With explicit gate mode and license allow-list:
bash scripts/mcp/run_mcp_eval.sh \
    --repo /path/to/mcp-server-source \
    --server-url http://localhost:8080/mcp \
    --mode warn --allow "apache-2.0 mit"
```

## Tested against

The pipeline has been verified against two MCP servers:

**generic-mock-mcp-server** — a configurable mock that serves static responses
via Streamable HTTP. Good for fast local testing.
```bash
git clone https://github.com/generic-mock-mcp-server /tmp/generic-mock-mcp-server
cd /tmp/generic-mock-mcp-server && uv venv && uv pip install -r requirements.txt
uv run python src/server.py --schema tests/schema.json --transport streamable-http --port 8080

# In another terminal:
bash scripts/mcp/run_mcp_eval.sh \
    --repo /tmp/generic-mock-mcp-server \
    --server-url http://localhost:8080/mcp
```

**openshift-mcp-server** — a real Go-based MCP server (Kubernetes tools,
Streamable HTTP). Tests against a real server with 14+ tools and pagination.
```bash
# Pre-built binary (or build from source):
/tmp/omcp --config /tmp/omcp.toml  # port 8081

bash scripts/mcp/run_mcp_eval.sh \
    --repo /tmp/openshift-mcp-server \
    --server-url http://localhost:8081/mcp
```

## On a cluster (Tekton)

Three Tekton Tasks run inside the eval container image
(`quay.io/ecosystem-appeng/abevalflow-mcp-eval:0.1`):

- `mcp-static-eval` — scanners + static evaluation gate
- `mcp-conformance-eval` — probe + compass + conformance evaluation gate
- `mcp-behavioral-eval` — mcpchecker + behavioral evaluation summary

Task YAMLs: `pipeline/tasks/konflux/mcp-static-eval.yaml`,
`mcp-conformance-eval.yaml`, and `mcp-behavioral-eval.yaml`.

## Future repo split

If Tekton task YAMLs move to a separate repo, the Python code stays here (baked
into the eval container image). The new repo has only YAML — it calls these
entry points via `python -m`. Zero code changes on either side.
