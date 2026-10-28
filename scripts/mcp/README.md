# `scripts/mcp/` — MCP evaluation pipeline runners

CLI entry points and supporting scripts for the MCP-server evaluation pipeline.
The evaluation runners are the **public API of the eval container image**:
Tekton task steps invoke them as `python -m scripts.mcp.<subpackage>.<name>`.
They run external tools, invoke evaluation logic from `abevalflow.mcp`, and
write result JSON. This directory also contains local testing, Compass setup,
and Pipeline generation utilities; those are not evaluation task runners.

## Structure (organized by role and evaluation type)

| Directory | Role | What it does |
|-----------|------|-------------|
| `scanners/` | **Scan** source code | Shell out to external tools (gitleaks, semgrep, licensee) and write normalized scan JSON. Static evaluation only — no running server needed. |
| `runners/` | **Run** evaluations and publish results | Thin CLIs that invoke gate logic from `abevalflow.mcp.gates`, write summary JSON, or publish Compass facts. All evaluation types. |
| `probes/` | **Probe** the live server | Connect to the running MCP server (or Compass API) and write per-check result JSON. Conformance evaluation only — server must be running. |
| `rules/` | **Bundled rulesets** | Offline semgrep rules for the no-user-code scan. Not a runner — consumed by the scanner. |

### `scanners/` — Static evaluation scanner runners

| File | What it does | Emits |
|------|-------------|-------|
| `static_eval_secrets_scan.py` | Shells out to gitleaks | `secrets-scan.json` |
| `static_eval_no_user_code_scan.py` | Shells out to semgrep (bundled offline ruleset) | `no-user-code-scan.json` |
| `static_eval_license_scan.py` | Shells out to licensee (top-level license only) | `license-scan.json` |
| `helpers.py` | `run_tool()` + `write_scan()` — scanner I/O helpers | — |

### `runners/` — evaluation and publishing runners

| File | What it does | Emits |
|------|-------------|-------|
| `static_eval_gate.py` | Calls `abevalflow.mcp.gates.run_static_eval()` | `static-eval-summary.json` |
| `conformance_eval_gate.py` | Calls `abevalflow.mcp.gates.run_conformance_eval()` | `conformance-eval-summary.json` |
| `behavioral_eval_summary.py` | Maps mcpchecker results to unified summary | `behavioral-eval-summary.json` |
| `compass_publish.py` | Maps phase summaries to SoundCheck facts (dry-run by default) | SoundCheck facts (preview or API write) |

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
| `abevalflow/mcp/` | **What** to decide | Gates, checks, types, client, phase configuration, Compass mapping — reads JSON, decides pass/fail |
| `scripts/mcp/` (here) | **How** to execute and maintain it | CLI entry points that run tools and write results, plus local testing, setup, and Pipeline generation utilities |

Evaluation data crosses the boundary through **JSON files on disk**: writers
produce scan/check results and phase summaries; readers consume them. Phase
configuration is loaded separately from `abevalflow/mcp/`.

## Phase configuration and Pipeline generation

The phase definitions in `abevalflow/mcp/phases/*.json` are the source of
truth for which evaluation phases exist. They are loaded by
`abevalflow/mcp/phase_config.py` alongside shared configuration from
`abevalflow/mcp/phase_registry.json`. The same phase configuration drives
Compass fact mapping, Pipeline generation, and the list of phase Tasks included
in the OCI Bundle.

Each phase definition provides metadata (`order`, `id`, `display_name`,
`description`, and `source`), check-to-fact mappings (`checks` and
`summary_fact_ref`), and a `task` binding with the Tekton Task name, YAML file,
parameter values, and workspace mappings. See the existing phase JSON files
for the supported shape and optional fields, including consumed Compass checks.

### Phase definition fields

Each `abevalflow/mcp/phases/*.json` file defines one phase. The loader validates
unique phase IDs, ordering values, summary fact references, and published check
fact references. The following fields are relevant when extending the system:

| Field | Purpose |
|-------|---------|
| `order` | Required unique integer; determines the generated Task list and Compass track level order. It does **not** create an execution dependency. |
| `id` | Required unique phase ID (letters, digits, `_`, `-`; must start with a letter or digit). Must match the summary JSON `phase` value and filename prefix. |
| `display_name`, `description` | Human-readable Compass track level metadata; set both for a new phase. |
| `source` | Default `source` for result checks in this phase (`pipeline` if omitted); individual checks may override it. |
| `summary_fact_ref` | Required unique Compass fact reference for the phase summary. |
| `checks` | Required map keyed by the check IDs emitted in the summary JSON (`checks[].check`). A published check needs `name`, `fact_ref`, `track_name`, and `description` to provide both result metadata and a SoundCheck track check. |
| `checks.<id>.source` | Optional per-check source override. |
| `checks.<id>.consumed` | Set to `true` for a result obtained from an existing Compass check. Do **not** set `fact_ref` for a consumed check; it is not published as an individual fact. |
| `consumed_track_checks` | Optional array of existing Compass-owned track checks (`id`, `name`, `description`) to reference in this phase's track level. It does not register or execute those checks. |
| `task.name`, `task.file` | Required Tekton Task name and repository-relative YAML path. Task YAML must live under `pipeline/tasks/konflux/` and must already exist. |
| `task.params`, `task.workspaces` | Mappings from the Task's declared parameter/workspace names to Pipeline values/workspaces. Include every required Task input; the generator validates the Task-side interface. |
| `depends_on` | Optional list of **earlier** phase IDs. The generator converts it to `runAfter`; without it, the phase has no generated phase-to-phase dependency. |

`abevalflow/mcp/phase_registry.json` contains the shared `overall_fact_ref`
for the combined evaluation summary. Do not add phase-specific definitions to
that file. `MCP_PHASE_CONFIG` and `MCP_PHASES_DIR` can override the paths for
isolated configuration tests.

`order` controls ordering in the generated YAML and Compass levels; execution
ordering is controlled separately by `depends_on`. The loader requires each
listed dependency to precede the current phase in configuration order.

`pipeline/pipelines/mcp-eval-pipeline.template.yaml` holds the shared Pipeline
configuration. `scripts/mcp/generate_mcp_pipeline.py` inserts phase Tasks into
the template and writes `pipeline/pipelines/mcp-eval-pipeline.yaml`. The output
is generated: **edit the template or phase configuration, not the generated
Pipeline by hand**. Each phase Task uses the Tekton bundle resolver and the
configured Task name. The Compass publishing Task remains in the template's
`finally` block, separate from the phase Task list.

Run these commands from the repository root:

```bash
# Regenerate the Pipeline after changing a phase or the template:
python -m scripts.mcp.generate_mcp_pipeline

# Check that the generated Pipeline is current and Task interfaces match:
python -m scripts.mcp.generate_mcp_pipeline --check

# Show the phase Task files collected for the Bundle:
python -m scripts.mcp.generate_mcp_pipeline --task-files

# Preview the Bundle publish command without publishing:
make -n -C pipeline/integration bundle-mcp-eval VERSION=0.1
```

The generator checks that each referenced Task YAML exists, has the configured
name, and declares the parameters and workspaces required by the phase binding.
It **does not implement new Task logic** or execute the Pipeline in a cluster.

## Running locally

`run_mcp_eval.sh` is the local dev/testing runner. It exercises the scanner,
probe, and gate code paths used by the Tekton Tasks, but does not validate
Tekton orchestration, bundle resolution, or cluster credentials.

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

The local evaluation runners have been tested against two MCP servers.
These tests do not constitute a full Tekton PipelineRun:

**generic-mock-mcp-server** — a configurable mock that serves static responses
via Streamable HTTP. Good for fast local testing.
```bash
# Given an existing checkout at /tmp/generic-mock-mcp-server:
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

The MCP evaluation OCI Bundle contains **four Tasks and one Pipeline**:

- `mcp-static-eval` — scanners + static evaluation gate
- `mcp-conformance-eval` — probe + compass + conformance evaluation gate
- `mcp-behavioral-eval` — mcpchecker + behavioral evaluation summary
- `mcp-compass-publish` — publishes mapped SoundCheck facts from phase summaries
- `mcp-eval-pipeline` — orchestrates the phase Tasks and Compass publishing

Task YAMLs live under `pipeline/tasks/konflux/`; the Pipeline lives at
`pipeline/pipelines/mcp-eval-pipeline.yaml` (generated from the template).
Phase Tasks are resolved from the configured `task-bundle` via Tekton's bundle
resolver. The Pipeline uses `onError: continue` for phase Tasks and invokes
Compass publishing from `finally`, after the phase Tasks have finished or
failed. Publication is dry-run by default.

Task steps use the evaluation container image, configured by the Pipeline
(default example: `quay.io/ecosystem-appeng/abevalflow-mcp-eval:0.1`). Changes
to code inside that image require rebuilding and publishing an updated image;
changing the Bundle alone does not update the image.

The Bundle target is `make -C pipeline/integration bundle-mcp-eval VERSION=0.1`.
Unlike `make -n`, this **pushes to the configured registry** and requires
appropriate credentials. The Bundle includes the phase Task YAMLs selected by
the registry and the shared Compass Task and Pipeline.

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
`abevalflow/mcp/checks/compass_publish.py`. Phase-specific check names, fact
references, and summary fact references come from the phase definitions in
`abevalflow/mcp/phases/`, rather than a fixed mapping in the publisher. The
CLI entry point is `scripts/mcp/runners/compass_publish.py`; the Tekton task is
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

The runner accepts explicit summary paths or a scoped report directory. In
directory mode, it discovers `*-summary.json` recursively beneath that
directory only. Summary filenames use `<phase-name>-summary.json`; the phase
name must match the JSON `phase` field. It publishes mapped individual checks,
one summary fact per mapped phase, and one combined summary fact to the
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
  --summary-files reports/my-server/static-eval-summary.json reports/my-server/conformance-eval-summary.json reports/my-server/behavioral-eval-summary.json \
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

### Adding a phase

New phases are discovered from `abevalflow/mcp/phases/*.json`. The Pipeline
generator, Bundle Task-file list, Compass publisher mappings, and SoundCheck
track setup all use this configuration; **no new phase-specific condition is
needed in those Python components**. A JSON definition alone does **not**
implement a new evaluation or make its Task available at runtime.

For example, a fourth phase could use
`abevalflow/mcp/phases/04-performance-eval.json` with this definition. The
Task path below is illustrative: create that Task before running `--check`.

```json
{
  "order": 40,
  "id": "performance-eval",
  "display_name": "Phase 4 — Performance Evaluation",
  "description": "MCP server response latency checks.",
  "source": "pipeline",
  "summary_fact_ref": "abevalflow:default/mcp_phase4",
  "checks": {
    "latency-check": {
      "name": "Latency",
      "fact_ref": "abevalflow:default/mcp_latency",
      "track_name": "Latency",
      "description": "Measures MCP server response latency."
    }
  },
  "task": {
    "name": "mcp-performance-eval",
    "file": "pipeline/tasks/konflux/mcp-performance-eval.yaml",
    "params": {
      "mcp-eval-image": "$(params.mcp-eval-image)"
    },
    "workspaces": {
      "source": "workspace"
    }
  },
  "depends_on": ["behavioral-eval"]
}
```

To make it runnable end to end:

1. **Define the phase.** Create its JSON file with a unique `id`, `order`,
   summary fact reference, check IDs/fact references, and Task binding. Keep
   check IDs consistent with the IDs produced by the evaluation. Add
   `consumed: true` entries only for checks read from Compass; if they should
   appear in the SoundCheck track, also list their existing Compass check
   definitions under `consumed_track_checks`.
2. **Implement its evaluation and Task.** Create the referenced Task YAML
   under `pipeline/tasks/konflux/`, with matching `metadata.name`, declared
   params and workspaces, and steps that run the evaluation. Put any new
   runtime code or dependencies in the evaluation image. The Task must write a
   `<phase-id>-summary.json` file into the shared reports workspace so the
   Compass `finally` Task can discover it. Follow the existing summary schema,
   including `phase`, `passed`, and a `checks` array containing `check`,
   `status` (`pass`, `fail`, or `not_evaluated`), and the result metadata.
3. **Wire shared inputs and dependencies.** Add any new Pipeline parameters
   or workspaces to `pipeline/pipelines/mcp-eval-pipeline.template.yaml`.
   Map those inputs in `task.params` and `task.workspaces`. Set `depends_on`
   explicitly when a phase must follow another; `order` alone does not impose
   a Tekton `runAfter` dependency. Keep the shared Compass publisher in the
   template's `finally` block.
4. **Generate and validate.** From the repository root, run:

   ```bash
   python -m scripts.mcp.generate_mcp_pipeline
   python -m scripts.mcp.generate_mcp_pipeline --check
   python -m scripts.mcp.generate_mcp_pipeline --task-files
   make -n -C pipeline/integration bundle-mcp-eval VERSION=0.1
   ```

   Commit the new definition, Task YAML, template changes (if any), and
   regenerated `pipeline/pipelines/mcp-eval-pipeline.yaml`. Run relevant
   tests and review the resolved Task parameters/workspaces. `--check`
   validates the generated file and Task interfaces; it does not run Tekton.
5. **Package and provision.** Rebuild and publish the evaluation image when
   its code **or bundled phase JSON** changes (the image copies
   `abevalflow/mcp/`). Publish an updated OCI Bundle containing the generated
   Pipeline, configured phase Tasks, and the Compass publishing Task. Preview
   the updated SoundCheck track/check definitions using
   `scripts/mcp/bootstrap_compass_track.py`; register them in each target
   environment separately, with explicit authorization for live writes.
6. **Verify the integration.** Test the new Task and summary locally, preview
   Compass facts (dry-run), then verify a Tekton PipelineRun with the new
   image/Bundle in a suitable cluster. The generator and local Bundle checks
   do not prove that runtime dependencies, permissions, and cluster execution
   work.

The Compass publisher discovers `*-summary.json` files inside its configured
reports directory; it does not need a hard-coded phase list. Each filename
prefix must equal the JSON `phase` field. Individual facts for unmapped or
`not_evaluated` checks are skipped; consumed Compass checks are never
re-published individually. A phase summary fact and the combined summary fact
use the configured `summary_fact_ref` and `overall_fact_ref`. Adding a summary
file alone does not register new Compass track checks or facts.

The following is a prior staging validation of the publishing flow, not a
full Tekton PipelineRun or a live publish test of the current config-driven
version. The staging validation used
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

The Tekton task is prepared with dry-run enabled by default. The generated
Pipeline places it in `finally`; before automated live publishing, verify
Bundle resolution, summary availability, and that the cluster secret
`compass-api-credentials` is configured with appropriate access. Track/check
provisioning remains a separate manual step. The generated Bundle has been
published to and listed from a local registry; GitHub Actions execution and a
full cluster PipelineRun have not yet been verified.

## Future repo split

If Tekton task YAMLs move to a separate repo, the Python entry points can
remain here, baked into the eval container image. The task definitions can
continue invoking them via `python -m`, but Pipeline generation, Task file
paths, and the Bundle build workflow would need to be adjusted for the new
repository layout.
