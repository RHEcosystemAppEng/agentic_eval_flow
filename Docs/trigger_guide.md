# How to Submit a Skill for A/B Evaluation

This guide explains how to submit a skill to the Agentic Eval Flow pipeline for
automated evaluation. By the end, you'll know what files to prepare, how to
submit them, and what happens next.

---

## What is this pipeline?

Agentic Eval Flow automatically tests whether an AI agent performs better **with**
your skill than **without** it. It does this by running the same task many
times in two configurations:

```
                  ┌──────────────────────┐
                  │   You push a skill   │
                  │   submission folder  │
                  └──────────┬───────────┘
                             │
                  ┌──────────▼───────────┐
                  │  Pipeline validates  │
                  │  your files          │
                  └──────────┬───────────┘
                             │
               ┌─────────────┴─────────────┐
               │                           │
    ┌──────────▼──────────┐     ┌──────────▼──────────┐
    │   Treatment (WITH   │     │   Control (WITHOUT   │
    │   your skill)       │     │   your skill)        │
    │   × K trials        │     │   × K trials         │
    └──────────┬──────────┘     └──────────┬──────────┘
               │                           │
               └─────────────┬─────────────┘
                             │
                  ┌──────────▼───────────┐
                  │  Compare results:    │
                  │  pass rates, uplift, │
                  │  statistical tests   │
                  └──────────┬───────────┘
                             │
                  ┌──────────▼───────────┐
                  │  Report: PASS / FAIL │
                  │  (stored for history)│
                  └──────────────────────┘
```

**Treatment** = the agent has your skill loaded.
**Control** = the agent runs without it (baseline).

If the treatment performs significantly better, the skill **passes**.

## Manually trigger the AEH OpenShell OpenClaw flow

The AEH OpenShell profile is a single-sided OpenClaw evaluation. The checked-in
PipelineRun example is `pipeline/runs/openshell-openclaw-pipelinerun.yaml`.
Run these commands from the ABEvalFlow checkout:

```bash
# Replace [NAMESPACE] with the namespace where your PipelineRun and
# namespace-local OpenShell/MLflow services are deployed.
NS="[NAMESPACE]"

# Apply the profile pipeline (safe to repeat).
oc -n "$NS" apply -f pipeline/pipelines/ci-pipeline-openshell.yaml

# Confirm namespace-local credentials and certificates exist before starting.
oc -n "$NS" get secret openshell-gateway-mtls \
  forge-agent-upstream-tls openshell-credentials openshell-oidc-credentials

# Create the PipelineRun using the pinned OpenClaw image, namespace gateway,
# GLM-5.3, and MLflow settings from the checked-in example.
oc -n "$NS" create -f pipeline/runs/openshell-openclaw-pipelinerun.yaml
```

Do not put provider tokens or private keys in the PipelineRun YAML; keep them
in namespace Secrets. The example uses `aeh_openshell_openclaw`, submission
`openclaw-forge`, model `rits/zai-org/glm-5-3`, and the namespace MLflow service.

To monitor the run:

```bash
RUN=$(oc -n "$NS" get pipelinerun \
  -l tekton.dev/pipeline=abevalflow-pipeline-openshell \
  --sort-by=.metadata.creationTimestamp \
  -o jsonpath='{.items[-1:].metadata.name}')
tkn -n "$NS" pipelinerun logs "$RUN" -f
oc -n "$NS" get pipelinerun "$RUN" -o wide
```

The run is complete only after `prepare`, `evaluate`, `analyze`, `store`, and
`cleanup` finish. Check `evaluate` for gateway, GLM, and skill-loading errors;
check `store` for database and artifact publication errors. MLflow has no
public Route by default, so view it through a local port-forward:

```bash
oc -n "$NS" port-forward svc/abevalflow-mlflow 15000:5000
```

Then open <http://127.0.0.1:15000>.

---

## What you need to prepare

There are two submission modes:

### Mode 1: Manual (you provide everything)

You write the skill, a task description, and verification tests yourself.
This is the default and currently supported mode.

### Mode 2: AI-assisted (you provide only the skill)

You write just the skill file. The pipeline generates the task description
and tests automatically using an AI assistant. To use this mode, set
`generation_mode: ai` in your `metadata.yaml`.

> **Note:** AI-assisted mode is now working. Set `generation_mode: ai` in
> your `metadata.yaml` to use it.

---

## Step 1: Create your submission folder

Create a folder with your skill name. The structure depends on which evaluation
engine you're using:

### Harbor Format (full agent evaluation)

```
my-skill/
├── metadata.yaml          ← describes your submission (required)
├── instruction.md         ← the task the agent must solve (required in manual mode)
├── skills/
│   └── SKILL.md           ← your skill file (required)
├── tests/
│   └── test_outputs.py    ← pytest tests that verify the solution (required in manual mode)
│   └── llm_judge.py       ← LLM-based judge (optional)
├── docs/                  ← reference docs for the agent (optional)
└── supportive/            ← mock MCPs, data files (optional, <50MB)
```

### ASE Format (lightweight LLM-as-judge)

```
my-skill/
├── metadata.yaml          ← describes your submission (required)
├── skills/
│   └── SKILL.md           ← your skill file (required)
└── evals/
    ├── evals.json         ← evaluation prompts and assertions (optional, generated if missing)
    └── files/             ← test data files referenced by evals (optional)
```

See `examples/sample_skill/` for a minimal working example (manual mode).

### ASE (Agent Skills Eval) Mode

For lightweight LLM-as-judge evaluation without full container isolation,
use the ASE format:

```
submissions/<skill-name>/
├── metadata.yaml              # Required
├── skills/
│   └── SKILL.md               # Required -- skill definition
└── evals/
    ├── evals.json             # Optional -- generated if missing
    └── files/                 # Optional -- test data files
```

Trigger with `eval-engine=ase`:

```bash
tkn pipeline start abevalflow-pipeline \
  -p repo-url=https://github.com/RHEcosystemAppEng/skill-submissions.git \
  -p revision=main \
  -p submission-dir=my-skill \
  -p eval-engine=ase \
  -w name=shared-workspace,volumeClaimTemplateFile=pipeline/triggers/pvc-template.yaml \
  -n ab-eval-flow
```

If `evals/evals.json` is not provided, the pipeline generates it from
`SKILL.md` using an LLM.

### MCPChecker Mode (MCP Server/Agent Evaluation)

For evaluating AI agents that interact with MCP (Model Context Protocol)
servers, use MCPChecker format. This mode tests the agent's ability to use
MCP tools correctly and produce valid outputs.

```
submissions/<name>/
├── metadata.yaml              # Required -- eval_engine: mcpchecker
├── eval.yaml                  # Required -- MCPChecker evaluation config
├── mcp-config.yaml            # Required -- MCP server connection settings
└── tasks/
    └── *.yaml                 # Required -- at least one task definition
```

**eval.yaml example:**

```yaml
apiVersion: mcpchecker/v1
kind: Eval
metadata:
  name: my-mcp-eval
spec:
  agent:
    model: google:gemini-2.5-flash
  judge:
    model: openai:gpt-4o
  tasks:
    - tasks/health-check.yaml
```

**mcp-config.yaml example:**

```yaml
mcpServers:
  - name: my-server
    url: http://localhost:3000
```

**Task file example:**

```yaml
apiVersion: mcpchecker/v1
kind: Task
metadata:
  name: health-check
spec:
  prompt: Check if the MCP server is healthy
  assertions:
    - type: contains
      expected: healthy
```

Trigger with `eval-engine=mcpchecker`:

```bash
tkn pipeline start abevalflow-pipeline \
  -p repo-url=https://github.com/RHEcosystemAppEng/skill-submissions.git \
  -p revision=main \
  -p submission-dir=my-mcp-eval \
  -p eval-engine=mcpchecker \
  -p mcpchecker-agent-model=google:gemini-2.5-flash \
  -p mcpchecker-judge-model=openai:gpt-4o \
  -w name=shared-workspace,volumeClaimTemplateFile=pipeline/triggers/pvc-template.yaml \
  -n ab-eval-flow
```

MCPChecker is single-agent evaluation (no A/B comparison). Results are
scored by task pass rate and LLM judge verification. See
`examples/mcpchecker-skill/` for a complete example.

### A2A Format (Agent-to-Agent Evaluation)

For evaluating agents that communicate via the A2A (Agent-to-Agent) protocol,
use the A2A format. This mode tests how well your agent responds to requests
from other agents using standardized A2A messaging.

```
submissions/<name>/
├── metadata.yaml              # Required -- eval_engine: a2a
├── agent_card.json            # Required -- A2A agent card
├── skills/
│   └── SKILL.md               # Required -- skill definition
└── evals/
    └── evals.json             # Optional -- evaluation scenarios (generated if missing)
```

**agent_card.json example:**

```json
{
  "name": "my-a2a-agent",
  "description": "Agent that handles customer support queries",
  "version": "1.0.0",
  "capabilities": ["text-generation", "tool-use"],
  "endpoint": "http://localhost:8000/a2a"
}
```

**metadata.yaml for A2A:**

```yaml
name: my-a2a-agent
eval_engine: a2a
a2a_config:
  protocol_version: "1.0"
  timeout_seconds: 30
  max_turns: 10
```

Trigger with `eval-engine=a2a`:

```bash
tkn pipeline start abevalflow-pipeline \
  -p repo-url=https://github.com/RHEcosystemAppEng/skill-submissions.git \
  -p revision=main \
  -p submission-dir=my-a2a-agent \
  -p eval-engine=a2a \
  -w name=shared-workspace,volumeClaimTemplateFile=pipeline/triggers/pvc-template.yaml \
  -n ab-eval-flow
```

A2A evaluation tests both agent functionality and protocol compliance. See
`examples/a2a-skill/` for a complete example.

### AEH Format (Agent-Eval-Harness)

For evaluating agents using the Agent-Eval-Harness framework, use the AEH format.
This mode provides flexible judge-based evaluation with support for LLM judges,
custom scoring, and detailed per-case analysis. Trials run as Harbor jobs on
OpenShift (OpenShiftEnvironment).

**Verified smoke samples** (in [skill-submissions](https://github.com/RHEcosystemAppEng/skill-submissions)):

| Mode | Branch | `submission-dir` |
|------|--------|------------------|
| Single | `eval/aeh-hello-world-single` | `aeh-hello-world-single` |
| Pairwise | `eval/aeh-hello-world-pairwise` | `aeh-hello-world-pairwise` |

#### AEH single mode

```
submissions/<name>/
├── metadata.yaml              # Required -- eval_engine: aeh
├── eval.yaml                  # Required -- AEH evaluation config (judges, thresholds)
├── skills/                    # Optional -- nested or flat SKILL.md for treatment
│   └── …/SKILL.md
└── cases/
    └── case-001/
        ├── input.yaml         # Required -- task prompt / agent instruction
        └── annotations.yaml   # Optional -- ground truth for judges
```

**eval.yaml essentials** (LiteLLM model ids, not Anthropic native names):

```yaml
name: aeh-hello-world-single
skill: aeh-hello-world-single

runner:
  type: claude-code
  effort: low
  settings:
    permission_mode: bypassPermissions

models:
  skill: claude-sonnet           # LiteLLM alias (override via aeh-model-override)
  judge: claude-sonnet

dataset:
  path: cases

outputs:
  - path: output                 # Required for artifact collection / pairwise

judges:
  - name: exit_success
    type: check
    # …
  - name: file_created
    type: check
    # …
```

**Trigger single AEH** (params that work on OpenShift with in-cluster LiteLLM):

```bash
# Dev pipeline + personal namespace example (guy-ziv-evalflow).
# Prod: abevalflow-pipeline / ab-eval-flow
oc create -n guy-ziv-evalflow -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: aeh-single-
spec:
  pipelineRef:
    name: abevalflow-pipeline-dev
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/skill-submissions.git"
    - name: revision
      value: "eval/aeh-hello-world-single"
    - name: submission-dir
      value: "aeh-hello-world-single"
    - name: eval-engine
      value: "aeh"
    - name: aeh-mode
      value: "single"
    - name: pipeline-repo-revision
      value: "APPENG-5300/aeh-engine-integration"   # or main once merged
    - name: llm-model
      value: "claude-sonnet"
    - name: llm-api-base
      value: "http://litellm.ab-eval-flow.svc.cluster.local:4000"
    - name: llm-api-key
      value: "mock"
    - name: aeh-model-override
      value: "claude-sonnet"
    - name: aeh-image
      value: "quay.io/ecosystem-appeng/agent-eval-harness:v1.0.3"
  taskRunTemplate:
    serviceAccountName: pipeline
  timeouts:
    pipeline: 2h0m0s
    tasks: 1h30m0s
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: [ReadWriteOnce]
          resources:
            requests:
              storage: 5Gi
YAML
```

Or with `tkn`:

```bash
tkn pipeline start abevalflow-pipeline-dev \
  -p repo-url=https://github.com/RHEcosystemAppEng/skill-submissions.git \
  -p revision=eval/aeh-hello-world-single \
  -p submission-dir=aeh-hello-world-single \
  -p eval-engine=aeh \
  -p aeh-mode=single \
  -p pipeline-repo-revision=APPENG-5300/aeh-engine-integration \
  -p llm-model=claude-sonnet \
  -p llm-api-base=http://litellm.ab-eval-flow.svc.cluster.local:4000 \
  -p llm-api-key=mock \
  -p aeh-model-override=claude-sonnet \
  -p aeh-image=quay.io/ecosystem-appeng/agent-eval-harness:v1.0.3 \
  -w name=shared-workspace,volumeClaimTemplateFile=pipeline/triggers/pvc-template.yaml \
  -n guy-ziv-evalflow
```

AEH-specific parameters:
- `aeh-model-override`: Override `models.skill` from eval.yaml (use LiteLLM aliases such as `claude-sonnet`)
- `aeh-judge-model-override`: Override `models.judge` from eval.yaml
- `aeh-mode`: `single` (default) or `pairwise`
- `aeh-control-config` / `aeh-treatment-config`: Pairwise config filenames (defaults: `eval-control.yaml` / `eval-treatment.yaml`)
- `aeh-image`: Harbor trial image (use `quay.io/ecosystem-appeng/agent-eval-harness:v1.0.3` or newer)
- `aeh-runner`: Execution backend — `harbor` (default) or `openshell` for `aeh_openshell_openclaw`

**Note on execution backends:**
- **harbor** (default): Containerized execution in OpenShift trial pods via AEH’s OpenShiftEnvironment.
- **openshell**: Host orchestrator calls `python -m agent_eval.openshell.run` against the cluster OpenShell gateway (`openshell` namespace). Used only with `eval-engine=aeh_openshell_openclaw`.
- **vanilla**: Not yet implemented in Agentic Eval Flow.

#### AEH OpenShell OpenClaw (`eval-engine=aeh_openshell_openclaw`)

Forge OpenClaw evals talk to the **cluster NVIDIA OpenShell** already installed in namespace `openshell` (Helm chart, Kubernetes sandboxes — not forge-saw / KubeVirt). Evaluate only sets `OPENSHELL_GATEWAY_ENDPOINT`. See [infrastructure_ops.md](infrastructure_ops.md#openshell-gateway-for-openclaw-evals).

Sample in-repo: `submissions/openclaw-forge/` (`eval_engine: aeh_openshell_openclaw`, `runner.type: openclaw`).

Requires Secret `openshell-credentials` in the **same namespace as the PipelineRun** for Forge Graph scenes (`submissions/openclaw-forge`, `m365.seed: external`). Without `M365_ACCESS_TOKEN` and `M365_USER`, Evaluate now fails closed instead of producing a 1/5 scorecard of auth refusals.

Required keys: `M365_ACCESS_TOKEN`, `M365_USER`. Optional: `M365_TENANT_ID`, `M365_CLIENT_ID`, `M365_CLIENT_SECRET`. `M365_AUTH_HEADER_FILE` and `M365_GRAPH_CURL` are **not** Secret keys — AEH writes them inside the sandbox from the access token.

Do **not** `oc apply` `config/forge-saw/secret-openshell-credentials.yaml` while it still contains `<replace-with-…>` placeholders, and do not commit tokens.

```bash
# Replace [NAMESPACE] before running these commands.
NS="[NAMESPACE]"

# from a shell that already has real Graph env (values never printed by the helper)
./config/forge-saw/create-openshell-credentials.sh
# or:
oc create secret generic openshell-credentials -n "$NS" \
  --from-literal=M365_ACCESS_TOKEN=... \
  --from-literal=M365_USER=... \
  --from-literal=M365_TENANT_ID=... \
  --from-literal=M365_CLIENT_ID=... \
  --from-literal=M365_CLIENT_SECRET=...
```

Client mTLS (`openshell-mtls`) is not required while the cluster gateway has TLS disabled.

**Use the OpenShell profile** (`abevalflow-pipeline-openshell`) instead of toggling `enable-ai-generation` / quality-review flags on `abevalflow-pipeline-dev`. That Pipeline is prepare → evaluate → analyze → store and **does not include the test cube** (security scan, quality review, AEH eval-check) or red-team. Harbor AEH keeps `abevalflow-pipeline` / `abevalflow-pipeline-dev`.

```bash
oc -n "$NS" apply -f pipeline/pipelines/ci-pipeline-openshell.yaml
oc create -n "$NS" -f pipeline/runs/openshell-openclaw-pipelinerun.yaml
```

Or inline:

```bash
oc create -n "$NS" -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: aeh-openshell-openclaw-
spec:
  pipelineRef:
    name: abevalflow-pipeline-openshell
  params:
    - name: submission-dir
      value: "openclaw-forge"
    - name: eval-engine
      value: "aeh_openshell_openclaw"
    - name: revision
      value: "feat/aeh-openshell-openclaw"
    - name: pipeline-repo-revision
      value: "feat/aeh-openshell-openclaw"
    - name: openshell-gateway-endpoint
      value: "http://openshell.openshell.svc.cluster.local:8080"
    - name: openshell-sandbox-image
      value: "ghcr.io/rh-forge/openclaw-saw-agent@sha256:bcc55e9b7a36d5f65e8ffc75962496f8b3617762a4cdb37fd1cf54611b72d41a"
    - name: aeh-openshell-image
      value: "registry.access.redhat.com/ubi9/python-311:9.6"
    - name: llm-model
      value: "rits/zai-org/glm-5-3"
    - name: llm-api-base
      value: "http://litellm.[NAMESPACE].svc.cluster.local:4000"
    - name: llm-api-key
      value: "mock"
    - name: aeh-model-override
      value: "inference/rits/zai-org/glm-5-3"
    - name: enable-mlflow
      value: "true"
    - name: mlflow-tracking-uri
      value: "http://abevalflow-mlflow.[NAMESPACE].svc.cluster.local:5000"
  taskRunTemplate:
    serviceAccountName: pipeline
  timeouts:
    pipeline: 2h0m0s
    tasks: 1h30m0s
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: [ReadWriteOnce]
          resources:
            requests:
              storage: 5Gi
YAML
```

Profile defaults (override only what you need): `eval-engine=aeh_openshell_openclaw`, `submission-dir=openclaw-forge`, the pinned GHCR sandbox image, orchestrator `registry.access.redhat.com/ubi9/python-311:9.6`, `enable-ai-generation=false`, **`enable-mlflow=true`** with tracking URI `http://abevalflow-mlflow.[NAMESPACE].svc.cluster.local:5000`. One PipelineRun becomes one MLflow experiment (name = Tekton run id). Port-forward the tracking server (no public Route by default): `oc -n "$NS" port-forward svc/abevalflow-mlflow 15000:5000`. Harness revision defaults to `feat/aeh-openshell-openclaw` (the OpenShell module lives there). After this branch merges, `revision` / `pipeline-repo-revision` can stay at Pipeline default `main`.

**MLflow: AEH vs CI (both used on this profile)**

| | **AEH (`/eval-mlflow`, `log_results.py`)** | **CI (`log_aeh_mlflow.py`)** |
|---|---|---|
| When | After a harness run (`eval-run` / OpenShell `score.py` artifacts exist) | After the evaluate Task, if `enable-mlflow=true` |
| What | Full AEH payload: params, judge metrics, `summary.yaml` / `report.html`, per-case table, traces built from `stdout.log` / events | Calls **the same** AEH `log_results.py` from the cloned harness (`AGENT_EVAL_HARNESS_ROOT`). If that no-ops, logs a **minimal** CI fallback (`mean_reward`, tokens, those files) |
| Experiment | `eval.yaml` `mlflow.experiment` (here `forge-eval-rubrics`) | Overridden to the **Tekton PipelineRun name** so cluster runs do not collide |
| Tracking URI | `mlflow.tracking_uri` in yaml, else `MLFLOW_TRACKING_URI`, else `http://127.0.0.1:5000` | Pipeline param `mlflow-tracking-uri` (exported as `MLFLOW_TRACKING_URI` for AEH too) |

OpenClaw does **not** emit live Claude-Code OTel traces inside the sandbox (that path is `execute.py` / Claude Code). AEH still **reconstructs** traces after harvest. CI does not replace AEH MLflow; it **invokes** it and only fills gaps.

**Store / artifacts:** OpenShell reuses the AEH MinIO prefix `{prefix}/debug/aeh/<run-id>/` (same as Harbor AEH run trees). Harbor `_eval_tmp` debug (`debug/harbor/`) is N/A. Cluster Postgres must have **Alembic 005** (`evaluation_runs.eval_engine` varchar(50)) before store-to-db succeeds — `aeh_openshell_openclaw` is 22 characters. The store Task uploads MinIO **before** the DB insert so a varchar(10) failure does not skip artifacts; the PipelineRun still fails until 005 is applied. See [persistence.md](persistence.md) and `alembic/versions/005_widen_eval_engine.py`.

Konflux evaluate has no AEH OpenShell path; cluster `ci-pipeline-openshell` is enough for this engine.

#### AEH Pairwise A/B Testing

Pairwise runs control then treatment on the same cases, then `score.py pairwise`
(LLM judge, position-swapped). After compare, the pipeline regenerates treatment
`report.html` with `--baseline` so the HTML includes the pairwise section.

**Pairwise submission structure:**

```
submissions/<name>/
├── metadata.yaml              # Required -- eval_engine: aeh
├── eval-control.yaml          # Required -- control/baseline (often skill: "")
├── eval-treatment.yaml        # Required -- treatment (with skill package)
├── skills/<name>/SKILL.md     # Treatment skill (optional nested layout)
└── cases/
    └── case-001/
        └── input.yaml
```

Both configs must share the same `skill:` namespace used for
`$AGENT_EVAL_RUNS_DIR/<skill>/<run-id>/`. Control may set `skill: ""` for an
unskilled baseline while treatment sets the real skill package name -- the
pipeline still keys runs under the treatment skill name.

**outputs:** is required when using a pairwise LLM judge (artifacts must be
bridged into `cases/<id>/<path>/` for the judge).

**Trigger pairwise** (same LiteLLM / image params as single):

```bash
oc create -n guy-ziv-evalflow -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: aeh-pairwise-
spec:
  pipelineRef:
    name: abevalflow-pipeline-dev
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/skill-submissions.git"
    - name: revision
      value: "eval/aeh-hello-world-pairwise"
    - name: submission-dir
      value: "aeh-hello-world-pairwise"
    - name: eval-engine
      value: "aeh"
    - name: aeh-mode
      value: "pairwise"
    - name: pipeline-repo-revision
      value: "APPENG-5300/aeh-engine-integration"
    - name: llm-model
      value: "claude-sonnet"
    - name: llm-api-base
      value: "http://litellm.ab-eval-flow.svc.cluster.local:4000"
    - name: llm-api-key
      value: "mock"
    - name: aeh-model-override
      value: "claude-sonnet"
    - name: aeh-image
      value: "quay.io/ecosystem-appeng/agent-eval-harness:v1.0.3"
  taskRunTemplate:
    serviceAccountName: pipeline
  timeouts:
    pipeline: 2h0m0s
    tasks: 1h30m0s
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: [ReadWriteOnce]
          resources:
            requests:
              storage: 5Gi
YAML
```

**Pairwise results:**

| Field | Meaning |
|-------|---------|
| `wins_a` | Treatment preferred |
| `wins_b` | Control preferred |
| `ties` | No preference |
| `win_rate` | `wins_a / (wins_a + wins_b + ties)` -- ties are non-wins |

Recommendation: pass when treatment wins ≥50% of cases, **or** when the run is
all-ties (no decisive losses). Scores still report the honest `win_rate` (e.g.
`0/1` tie → `0%` with recommendation `pass`).

**Where pairwise appears in MinIO:**

| Path | Contents |
|------|----------|
| `debug/aeh/treatment-*/summary.yaml` → `pairwise:` | Wins/ties + LLM reasoning (AEH native) |
| `debug/aeh/treatment-*/report.html` | HTML regenerated with `--baseline` (includes pairwise) |
| `report.json` → `pairwise` | Aggregated Agentic Eval Flow report |
| `debug/harbor/control|treatment/<timestamp>/` | Raw Harbor trial trees |
| `debug/aeh/control-*/` / `treatment-*/` | AEH mapped runs (`summary.yaml`, `run_result.json`, `cases/`) |

See skill-submissions branches above for complete samples. Or use
`./scripts/misc/trigger_test_runs.sh` (includes AEH single + pairwise).

### AI-Assisted Mode

If you only have the skill definition and want the pipeline to generate the
instruction and tests automatically, set `generation_mode: ai` in
`metadata.yaml` and provide only `skills/SKILL.md`:

```
submissions/<skill-name>/
├── metadata.yaml              # Must include: generation_mode: ai
└── skills/
    └── SKILL.md               # Required -- the pipeline generates the rest
```

**Harbor mode:** The pipeline will use an LLM to generate `instruction.md` and
`tests/test_outputs.py` from the skill definition before validation.
See `examples/sample_skill_ai/` for a minimal AI-mode example.

**ASE mode:** The pipeline will generate `evals/evals.json` from `SKILL.md` if
not provided. No `instruction.md` or `test_outputs.py` needed.

To enable AI-assisted features, pass the feature flags when triggering:

```bash
tkn pipeline start abevalflow-pipeline \
  -p repo-url=https://github.com/RHEcosystemAppEng/skill-submissions.git \
  -p revision=main \
  -p submission-dir=my-skill \
  -p enable-ai-generation=true \
  -p enable-ai-review=true \
  -w name=shared-workspace,volumeClaimTemplateFile=pipeline/triggers/pvc-template.yaml \
  -n ab-eval-flow
```

### metadata.yaml (required)

At minimum, you only need a name:

```yaml
name: my-skill
```

A more complete example:

```yaml
name: my-skill
description: Teaches the agent to generate Kubernetes manifests
persona: rh-developer
version: "0.1.0"
author: Jane Doe
tags:
  - kubernetes
  - openshift
```

For AI-assisted mode:

```yaml
name: my-skill
generation_mode: ai
```

For security scan configuration:

```yaml
name: my-skill
security_scan: warn  # Options: disabled, warn (default), block
```

For gate policy configuration (controls how evaluation gates combine):

```yaml
name: my-skill
gate_policy:
  default_mode: warn          # disabled | warn | block (default: warn)
  combination: all_pass       # all_pass | any_pass | weighted (default: all_pass)
  gates:
    evaluation:
      mode: block             # Engine gate must pass for approval
      threshold: 0.0          # Minimum uplift threshold
    security:
      mode: warn              # Security findings are advisory
    quality:
      mode: warn              # Quality review is advisory
      threshold: 0.6          # Minimum quality score
```

Gate modes:
- `disabled` -- Gate is skipped entirely
- `warn` -- Gate runs but failures don't block the pipeline
- `block` -- Gate failures cause the scorecard to fail

Combination modes:
- `all_pass` -- All blocking gates must pass (default)
- `any_pass` -- At least one blocking gate must pass
- `weighted` -- Weighted average of scores (≥0.7 pass, 0.5-0.7 warn, <0.5 fail)

For Compass Facts integration (pushing evaluation results to Red Hat Compass):

```yaml
name: my-skill
gate_policy:
  push_facts:
    endpoint: https://compass.redhat.com/api/soundcheck/facts/
    entity_ref: component:default/my-skill
    fact_ref_prefix: catalog:default/abevalflow_
    bearer_token: ${COMPASS_API_TOKEN}
  gates:
    evaluation:
      mode: block
      push_fact: true
    security:
      mode: warn
      push_fact: true
```

When `push_fact: true` is set for a gate, the pipeline pushes the gate result as a
Soundcheck fact to Compass after evaluation completes. The `entity_ref` identifies
your component in Compass, and `fact_ref_prefix` is prepended to the gate name
(e.g., `catalog:default/abevalflow_evaluation`).

**Name rules:** lowercase letters, numbers, hyphens, dots, and underscores
only. Must start with a letter or number. Examples: `my-skill`,
`k8s-manifest-gen`, `ocp.admin.tool`.

### instruction.md (required in manual mode)

A clear description of the task the agent must complete. Write it as if
you're explaining the task to a developer. Example:

```markdown
# Create a Greeting Module

Create a `greeting.py` module with a `greet(name: str) -> str` function
that returns a personalized greeting.

## Requirements

- Accept a single `name` argument
- Return format: "Hello, {name}! Welcome aboard."
- Handle empty string by returning "Hello, stranger! Welcome aboard."
```

### skills/SKILL.md (required)

The skill file that will be loaded into the agent during the **treatment**
runs. This is what you're evaluating -- the guidance that should make the
agent perform better. Example:

```markdown
# Greeting Module Skill

When asked to create a greeting module:

- Use a single function `greet(name: str) -> str`
- Default to "stranger" when the name is empty
- Keep the output friendly and professional
- Use f-strings for formatting
```

### tests/test_outputs.py (required in manual mode)

Standard pytest tests that verify the agent's output. These run
automatically after each trial. Example:

```python
import importlib
import sys
from pathlib import Path


def _load_module():
    sys.path.insert(0, str(Path("/workspace")))
    return importlib.import_module("greeting")


def test_greet_with_name():
    mod = _load_module()
    assert mod.greet("Alice") == "Hello, Alice! Welcome aboard."


def test_greet_empty_string():
    mod = _load_module()
    assert mod.greet("") == "Hello, stranger! Welcome aboard."
```

### docs/ (optional)

Reference documentation copied into both treatment and control containers
for the agent to consult during trials. Place any relevant `.md`, `.txt`,
or `.pdf` files here.

### supportive/ (optional)

Mock MCP servers, sample data files, or other supporting resources.
Must be under 50 MB total (enforced by validation).

---

## Step 2: Submit your skill

Push your folder to the submissions repository under the `submissions/`
directory:

```bash
# Clone the submissions repo (first time only)
git clone https://github.com/RHEcosystemAppEng/skill-submissions.git
cd skill-submissions

# Add your skill folder
cp -r ~/my-skill submissions/my-skill

# Push
git add submissions/my-skill/
git commit -m "Submit my-skill for evaluation"
git push
```

That's it. The push triggers the pipeline automatically.

---

## Step 3: Wait for results

After you push, the pipeline runs automatically:

1. **Validates** your files (structure, naming, tests compile)
2. **Generates** missing test artifacts if needed (instruction.md, evals.json)
3. **Reviews** submission quality (advisory, non-blocking)
4. **Scans** for security issues (optional)
5. **Evaluates** using Harbor (container-based) or ASE (LLM-as-judge)
6. **Analyzes** pass rates, computes uplift and statistical significance
7. **Stores** results to MinIO and PostgreSQL
8. **Reports** a PASS or FAIL recommendation

Typical runtime: **5-30 minutes** depending on evaluation engine and task complexity.

### Where to find results

- **Pipeline status:** visible in the OpenShift console under
  Pipelines > PipelineRuns in the `ab-eval-flow` namespace
- **Tekton results:** task outputs available in PipelineRun status

**MinIO (S3 object storage):**
- `report.json` / `report.md` -- evaluation report with pass rates, uplift, p-values
- `scorecard.json` -- unified verdict combining all evaluation gates (see below)
- `security-scan.json` / `security-scan.sarif` -- security scan findings
- `generated/` -- AI-generated files (instruction.md, test_outputs.py, evals.json)
- `debug/` -- engine-specific trial / run trees
  - **Harbor / A2A:** `debug/` trial trees (`agent/`, `verifier/`, …)
  - **AEH:** `debug/harbor/{control,treatment}/<timestamp>/` (raw Harbor jobs)
    and `debug/aeh/<run-id>/` (`summary.yaml`, `report.html`, `run_result.json`, `cases/`)

**PostgreSQL database:**
- `analysis_results` table -- evaluation summaries (pass rates, uplift, p-values)
- `security_scans` table -- security scan results per pipeline run
- Historical results queryable via `scripts/query_results.py`

### Understanding the Scorecard

The pipeline evaluates your submission through multiple **gates**, each checking
a different aspect:

| Gate Type | Gate Name | What it checks |
|-----------|-----------|----------------|
| **Engine** | `evaluation` | A/B evaluation results (Harbor, ASE, A2A, MCPChecker, AEH) |
| **Security** | `security` | Security vulnerabilities (Cisco scanner) |
| **Quality** | `quality` | Test coherence, coverage, clarity (LLM review) |

Each gate produces:
- `passed`: boolean (did it meet its threshold?)
- `score`: 0.0-1.0 normalized score
- `findings`: list of issues found (for security/quality gates)

The `scorecard.json` combines all gate results into a single recommendation:
- **pass** -- All blocking gates passed
- **warn** -- Warning gates failed but no blocking gates failed
- **fail** -- One or more blocking gates failed

Example scorecard output:
```json
{
  "recommendation": "pass",
  "recommendation_reason": "All gates passed",
  "gates_passed": 3,
  "gates_failed": 0,
  "gates": [
    {"gate_name": "evaluation", "passed": true, "score": 0.85},
    {"gate_name": "security", "passed": true, "score": 1.0},
    {"gate_name": "quality", "passed": true, "score": 0.78}
  ]
}
```

Configure gate behavior via `gate_policy` in your `metadata.yaml` (see above).

---

## Manual trigger (for testing / operators)

You can bypass the webhook and trigger the pipeline directly:

### Option 1: `tkn` CLI

```bash
tkn pipeline start abevalflow-pipeline \
  -p repo-url=https://github.com/RHEcosystemAppEng/skill-submissions.git \
  -p revision=main \
  -p skill-dir=my-skill \
  -w name=shared-workspace,volumeClaimTemplateFile=pipeline/triggers/pvc-template.yaml \
  -n ab-eval-flow
```

### Option 2: PipelineRun YAML

```yaml
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: abevalflow-manual-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-pipeline
  params:
    - name: repo-url
      value: https://github.com/RHEcosystemAppEng/skill-submissions.git
    - name: revision
      value: main
    - name: skill-dir
      value: my-skill
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes:
            - ReadWriteOnce
          resources:
            requests:
              storage: 1Gi
```

### Webhook configuration

Configure a GitHub webhook on the submissions repository:

| Setting      | Value                                                    |
|--------------|----------------------------------------------------------|
| Payload URL  | `https://<eventlistener-route>/`                         |
| Content type | `application/json`                                       |
| Events       | **Just the push event**                                  |
| Secret       | Shared secret (configure in EventListener if needed)     |

To find the EventListener route:

```bash
oc get route -n ab-eval-flow -l eventlistener=submission-listener
```

---

## Quick checklist

Before submitting, verify:

- [ ] `metadata.yaml` exists and has a valid `name`
- [ ] `skills/SKILL.md` exists and is non-empty
- [ ] `instruction.md` exists and clearly describes the task
- [ ] `tests/test_outputs.py` exists and runs with `pytest` locally
- [ ] No secrets, passwords, or API keys in any file
- [ ] `supportive/` folder (if present) is under 50 MB
- [ ] Folder name matches the `name` in `metadata.yaml`

---

## Example: complete sample submission

A working example is available in the repository:

```
examples/sample_skill/
├── metadata.yaml
├── instruction.md
├── skills/
│   └── SKILL.md
└── tests/
    └── test_outputs.py
```

You can copy this as a starting point:

```bash
cp -r examples/sample_skill submissions/my-new-skill
# Edit the files for your use case
```

---

## Frequently asked questions

**Q: What happens if my submission fails validation?**
The pipeline stops immediately and reports which checks failed (e.g.,
missing files, invalid metadata, tests that don't compile). Fix the
issues and push again.

**Q: Can I re-run an evaluation?**
Yes. Make any change to your submission folder and push again. Each push
triggers a new evaluation run.

**Q: How many trials are run?**
20 per variant by default (40 total). You can change this in
`metadata.yaml`:

```yaml
experiment:
  n_trials: 10
```

**Q: What counts as a "pass"?**
Each trial runs your tests against the agent's output. If the tests pass,
the trial passes. The overall evaluation compares treatment vs. control
pass rates and uses statistical tests (Fisher's exact test, t-test) to
determine if the improvement is significant.

**Q: Can I evaluate something other than a skill?**
Yes. The pipeline supports different experiment types (model comparison,
prompt comparison, custom). Set `experiment.type` in `metadata.yaml`.
See `Docs/trigger_models_and_experiment_types.md` for details.

**Q: Who do I contact for help?**
Reach out to the Agentic Eval Flow team or open an issue in the
[Agentic Eval Flow repository](https://github.com/RHEcosystemAppEng/agentic_eval_flow).

---

## Appendix: Operator Reference

This section is for platform operators who deploy and maintain the pipeline
infrastructure. Submitters can skip this.

### Webhook Configuration

Configure a GitHub webhook on the submissions repository:

| Setting      | Value                                                |
|--------------|------------------------------------------------------|
| Payload URL  | `https://<eventlistener-route>/`                     |
| Content type | `application/json`                                   |
| Events       | **Just the push event**                              |
| Secret       | Shared secret (configure in EventListener if needed) |

To find the EventListener route:

```bash
oc get route -n ab-eval-flow -l eventlistener=submission-listener
```

### Manual Trigger (for Testing)

#### Option 1: `tkn` CLI

```bash
tkn pipeline start abevalflow-pipeline \
  -p repo-url=https://github.com/RHEcosystemAppEng/agentic-collections.git \
  -p revision=main \
  -p submission-dir=my-skill \
  -w name=shared-workspace,volumeClaimTemplateFile=pipeline/triggers/pvc-template.yaml \
  -n ab-eval-flow
```

#### Option 2: PipelineRun YAML

```yaml
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: abevalflow-manual-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-pipeline
  params:
    - name: repo-url
      value: https://github.com/RHEcosystemAppEng/skill-submissions.git
    - name: revision
      value: main
    - name: submission-dir
      value: my-skill
    # Optional parameters:
    # - name: eval-engine
    #   value: harbor  # Options: harbor (default), ase, both
    # - name: llm-model
    #   value: claude-sonnet  # LLM model for evaluation
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes:
            - ReadWriteOnce
          resources:
            requests:
              storage: 1Gi
```

Apply with:

```bash
oc create -f pipelinerun.yaml -n ab-eval-flow
```

### Tekton Components

| Component | File | Purpose |
|-----------|------|---------|
| Pipeline | `pipeline/pipeline.yaml` | End-to-end pipeline wiring all tasks |
| EventListener | `pipeline/triggers/event-listener.yaml` | Receives webhooks, filters, extracts submission dir |
| TriggerBinding | `pipeline/triggers/trigger-binding.yaml` | Maps webhook payload to pipeline params |
| TriggerTemplate | `pipeline/triggers/trigger-template.yaml` | Creates PipelineRun from params |
| Validate Task | `pipeline/tasks/validate.yaml` | Validates submission structure and schema |
| Generate Tests | `pipeline/tasks/generate_tests.yaml` | AI-assisted test generation (optional) |
| Test Quality Review | `pipeline/tasks/test-quality-review.yaml` | AI quality review of submission (advisory, non-blocking) |
| Security Scan | `pipeline/tasks/security-scan.yaml` | Cisco AI Defense security scanning (optional) |
