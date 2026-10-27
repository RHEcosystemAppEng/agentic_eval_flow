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

The report model and fact mapping live in `abevalflow/mcp/eval_result.py` and
`abevalflow/mcp/checks/compass_publish.py`. The CLI entry point is
`scripts/mcp/runners/compass_publish.py`; the Tekton task is
`pipeline/tasks/konflux/mcp-compass-publish.yaml`.

### Track and check setup

The dedicated track is **ABEvalFlow MCP Evaluation** (`abevalflow-mcp-evaluation`),
owned by `group:redhat/ai5-marketplace` and filtered to MCP server entities.
It separates checks into three levels:

1. Phase 1 — Static Analysis
2. Phase 2 — Conformance Testing
3. Phase 3 — Behavioral Evaluation

The setup script, `scripts/mcp/bootstrap_compass_track.py`, is a manual,
per-environment provisioning step—not part of each evaluation run. It
registers twelve ABEvalFlow-owned check definitions, then creates the track or
updates its levels if it already exists. Two existing Compass checks
(`mcp-tool-schema-check` and `mcp-oauth-scope-match`) are referenced in the
Phase 2 level; their definitions are not recreated by this script.

Both setup and publishing are **dry-run by default**. Use `--no-dry-run` only
for an explicitly authorized live write. The API key must be provided via the
`COMPASS_API_KEY` environment variable; never put it in source, reports, or
command examples.

Preview setup requests:

```bash
python -m scripts.mcp.bootstrap_compass_track \
  --compass-url https://compass.stage.redhat.com/api/soundcheck
```

Actually provision a permitted environment:

```bash
python -m scripts.mcp.bootstrap_compass_track \
  --compass-url https://compass.stage.redhat.com/api/soundcheck \
  --no-dry-run
```

The bootstrap uses `POST /checks` with `{"checks": [...]}`. If that returns
HTTP 409 for existing check IDs, it falls back to `PUT /checks/{check-id}`.
It creates the track using `POST /tracks` with `{"tracks": [track]}`; if the
track already exists (HTTP 409), it updates it with `PUT /tracks/{track-id}`
and the track definition as the body.

### Preview and publish evaluation facts

<<<<<<< HEAD
The runner reads `static-eval-summary.json` and
`conformance-eval-summary.json` files from the reports directory. It also reads
`behavioral-eval-summary.json` when present. Legacy `phase1-summary.json`,
`phase2-summary.json`, and `phase3-summary.json` names remain supported. Files
can be flat in the reports directory or inside `phase1/`, `phase2/`, and
`phase3/` subdirectories. It publishes individual evaluated checks, one
summary fact per available phase, and one combined summary fact to the
=======
The runner accepts explicit summary paths or a scoped report directory. In
directory mode, it discovers `*-summary.json` recursively beneath that
directory only. Summary filenames use `<phase-name>-summary.json`; the phase
name must match the JSON `phase` field. It publishes mapped individual checks,
one summary fact per mapped phase, and one combined summary fact to the
>>>>>>> 6bdf460 (Generic phase loaders support any phase count. Compass SoundCheck publishing)
SoundCheck Facts API. The request shape is `POST /api/soundcheck/facts/` with a `facts` array;
each fact contains `factRef`, `entityRef`, and `data`. For example:

```json
{
  "facts": [{
    "factRef": "abevalflow:default/mcp_http_support",
    "entityRef": "mcpserver:default/my-server",
    "data": {
      "passed": true,
      "score": 1.0,
      "status": "pass",
      "message": "Server responded correctly over HTTP.",
      "source": "probe",
      "evaluated_at": "<UTC timestamp>"
    }
  }]
}
```

Preview (no HTTP write):

```bash
python -m scripts.mcp.runners.compass_publish \
  --reports-dir reports/my-server \
  --entity-ref mcpserver:default/my-server \
  --compass-url https://compass.stage.redhat.com/api/soundcheck/facts/
```

Pass exact summary files when a task already has their paths:

```bash
python -m scripts.mcp.runners.compass_publish \
  --summary-files reports/my-server/phase1-summary.json reports/my-server/phase2-summary.json \
  --entity-ref mcpserver:default/my-server \
  --compass-url https://compass.stage.redhat.com/api/soundcheck/facts/
```

Live publish requires explicit opt-in and the API key in the environment:

```bash
python -m scripts.mcp.runners.compass_publish \
  --reports-dir reports/my-server \
  --entity-ref mcpserver:default/my-server \
  --compass-url https://compass.stage.redhat.com/api/soundcheck/facts/ \
  --no-dry-run
```

Not-evaluated checks are omitted as individual facts. The two Compass-consumed
checks are included in the phase summary but are not re-published individually,
avoiding a circular write. The pipeline can read those existing results with
`GET /api/soundcheck/results?entityRef=<url-encoded-ref>&checks=mcp-tool-schema-check&checks=mcp-oauth-scope-match`.

<<<<<<< HEAD
### Optional behavioral phase

The pipeline runs behavioral evaluation only when its gating label is
present. The publisher includes Phase 3 when its summary file exists; otherwise
the combined summary marks Phase 3 as unavailable. The Compass track uses one
aggregate behavioral check, while its phase fact retains each behavioral check's
details.
=======
### Adding a phase

The loader and publisher accept any phase name with the standard summary
schema. Add phase configuration for check display names, consumed checks,
source defaults, check fact references, and the phase summary fact reference.
Unmapped facts are skipped with a warning. Compass track definitions remain a
separate registration concern.
>>>>>>> 6bdf460 (Generic phase loaders support any phase count. Compass SoundCheck publishing)

The staging validation used
`mcpserver:ai5-marketplace/openshift-mcp-server`. The local MCP endpoint served
its protocol/tool surface, but cluster authentication was unavailable, so no
Kubernetes operations were tested or performed. The publisher sent 11 facts;
all POSTs returned HTTP 200. Read-back returned HTTP 200 with 10 result
entries. Phase 1's secrets scan reported 112 high-severity matches (not
triaged here, so these are scanner findings, not confirmed exposed secrets).
Phase 2 had passing HTTP support, schema, annotations, and OpenAPI/MCP-native
contract checks; protocol compliance failed, and rate limiting, response-size
limit, and mandatory-timeout checks were not evaluated. The two existing
Compass-consumed results returned errors because expected catalog metadata was
missing; their definitions were not modified by the publisher.

The dedicated track is visible at:
<https://compass.stage.redhat.com/soundcheck/tracks/abevalflow-mcp-evaluation/overview?section=checks-by-levels>

The Tekton task is prepared with dry-run enabled by default. Before automated
live publishing, verify that the pipeline invokes it after the Phase 1 and 2
gates and that the cluster secret `compass-api-credentials` is configured with
appropriate access. Track/check provisioning remains a separate manual step.

## Future repo split

If Tekton task YAMLs move to a separate repo, the Python code stays here (baked
into the eval container image). The new repo has only YAML — it calls these
entry points via `python -m`. Zero code changes on either side.
