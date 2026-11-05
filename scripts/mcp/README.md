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

## Publishing results to Compass SoundCheck

The evaluation writes phase summaries locally first. The Compass publisher
converts those summaries into SoundCheck facts for the evaluated MCP catalog
entity. The API is the delivery mechanism; the code here makes publishing
repeatable for local and pipeline runs.

```text
Static evaluation ────────> static-eval-summary.json ──┐
Conformance evaluation ───> conformance-eval-summary.json ─┼─> publisher ─> SoundCheck facts
Behavioral evaluation ────> behavioral-eval-summary.json ─┘  (optional)
```

The fact generation logic lives in `scripts/mcp/fact_generator.py`. The CLI
entry point is `scripts/mcp/runners/generate_compass_facts.py`; the Tekton
tasks are `pipeline/tasks/konflux/generate-compass-facts.yaml` and
`pipeline/tasks/konflux/publish-to-compass.yaml`.

### Publishing evaluation facts

The publishing flow is split into two stages:

1. **Fact generation** (`generate_compass_facts.py`) — transforms phase summary
   JSON files into Compass fact payloads. Each fact file contains `factRef` and
   `data`, but **not** `entityRef` (that's injected at publish time from the
   target cluster Secret).

2. **Publishing** (`publish-to-compass.yaml` task) — creates a ConfigMap from
   fact files, launches a Kubernetes Job on the target cluster that injects
   `entityRef` from the Compass Secret and POSTs each fact to the SoundCheck
   API.

The runner accepts a directory containing phase summary JSON files
(`*-summary.json`). It generates mapped individual check facts, one phase
summary fact per phase, and one combined evaluation summary fact. Each fact
file has this structure (no `entityRef` yet):

```json
{
  "factRef": "abevalflow:default/mcp_http_support",
  "data": {
    "passed": true,
    "score": 1.0,
    "status": "pass",
    "message": "Server responded correctly over HTTP.",
    "source": "probe",
    "evaluated_at": "2026-10-29T12:00:00Z"
  }
}
```

Generate fact files locally:

```bash
python -m scripts.mcp.runners.generate_compass_facts \
  --summary-dir reports/my-server \
  --output-dir facts
```

The Tekton task handles publishing. It copies the Compass Secret from a
configured namespace, creates a ConfigMap with the fact files, and launches a
Job that injects `entityRef` and POSTs to Compass. Dry-run is enabled by
default.

Not-evaluated checks are omitted as individual facts. The two Compass-consumed
checks (`tool-name-rules`, `oauth-catalog-match`) are included in the phase
summary but are not re-published individually, avoiding a circular write. The
pipeline can read those existing results with `GET
/api/soundcheck/results?entityRef=<url-encoded-ref>&checks=mcp-tool-schema-check&checks=mcp-oauth-scope-match`.

### Adding a phase

The fact generator accepts any phase name with the standard summary schema. To
add a new phase, update `scripts/mcp/fact_generator.py` with:

1. **Check display names** in `PHASE_CHECK_NAMES`
2. **Consumed checks** (if any) in `PHASE_CONSUMED_CHECKS`
3. **Source defaults** in `PHASE_SOURCE_DEFAULTS` and `PHASE_CHECK_SOURCE_DEFAULTS`
4. **Fact ref mappings** in `PHASE_FACT_MAPS`
5. **Phase summary fact ref** in `PHASE_SUMMARY_FACTS`

Unmapped facts are skipped with a warning. Compass track and check definitions
are managed separately via infrastructure setup (not part of the evaluation
pipeline).

## Future repo split

If Tekton task YAMLs move to a separate repo, the Python code stays here (baked
into the eval container image). The new repo has only YAML — it calls these
entry points via `python -m`. Zero code changes on either side.
