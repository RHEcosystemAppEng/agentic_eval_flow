# Forge pointers and harness snapshot join contract

> Related: APPENG-4985 (persistence), APPENG-5370 Phase C (MLflow observer),
> APPENG-5300 (eval engine intake).
>
> **This document is the cross-project join contract.** Agent runtimes, eval
> engines, and certification pipelines link here. Do not invent a parallel
> schema elsewhere. Eval engines that already use “provenance” for
> *case-generation source* must keep that meaning separate from these forge
> join keys.

## Purpose

Three observability streams stay separate; **forge / harness join pointers**
correlate them:

| Stream | Typical artifact | Writer role |
|--------|------------------|-------------|
| Harness snapshot | `harness-snapshot.json` | Agent runtime (run start) |
| Runtime trace | OTLP / local telemetry JSONL; and/or MLflow GenAI traces | Agent runtime and/or eval engine |
| Eval verdict | Scorecard / MLflow metrics + judge feedback | Eval / certification pipeline |

`abevalflow.report.Provenance` is the **canonical typed join record** on an
analysis report (fields below). The harness snapshot JSON and MLflow tags use
the same semantic names (with the aliases noted in the field table).

## Ownership matrix

| Artifact | Writer | Canonical store | Reader discovery |
|----------|--------|-----------------|------------------|
| Harness snapshot | Agent runtime | Runtime run output directory; mirrored on OTel root-span attributes | File name `harness-snapshot.json` beside Level-1 telemetry |
| Snapshot on eval run | Eval engine MLflow logger | MLflow run **artifact** `harness-snapshot.json` + projected **tags** | Experiment + run name / `eval_run_id` (same join as `inputs/` artifacts) |
| Runtime OTel spans | Agent runtime | OTLP backend and/or `run-telemetry.jsonl` | Existing runtime tracing config |
| MLflow GenAI traces | Eval engine | MLflow experiment traces | `tracking_uri` + experiment; tag `eval_run_id` |
| `Provenance` on report | Certification / analyze pipeline | Analysis result / scorecard | ABEvalFlow report APIs |

**Disk under the eval run tree is only the handoff into the MLflow logger.**
After logging, **canonical read for join keys is MLflow** (artifact + tags),
not re-scraping CI environment variables.

## Field table

| Provenance / MLflow tag | Snapshot JSON (when present) | Required | OTel (when on spans) | Notes |
|-------------------------|------------------------------|----------|----------------------|-------|
| `generated_at` | `recorded_at` | yes (default now) | — | Report timestamp vs snapshot write time |
| `commit_sha` | `ref_revision` | no | `vcs.ref.head.revision` | Same semantic |
| `pipeline_run_id` | `pipeline_run_id` | no | `cicd.pipeline.run.id` | |
| `pipeline_run_url` | `pipeline_run_url` | no | — | Optional convenience URL |
| `repository_url` | `repository_url` | no | `vcs.repository.url.full` | |
| `change_id` | `change_id` | no | `vcs.change.id` | PR/MR number |
| `ref_name` | `ref_name` | no | `vcs.ref.head.name` | Branch/tag short name |
| `trace_id` | `trace_id` | no | trace id on root span | W3C / backend id |
| `session_id` | — | no | `gen_ai.conversation.id` | |
| `eval_run_id` | — | no | — | Eval batch/case id; MLflow run name / tag |
| `harness_fingerprint` | `harness_content_sha` | no | `fullsend.harness.content_sha` (runtime-specific attr ok) | Config content hash |
| `forge_platform` | `forge_platform` | no | runtime-specific | `github` \| `gitlab` \| `bitbucket` \| `forgejo` \| … |
| `eval_engine` | — | yes on Provenance (default `harbor`) | — | `harbor` \| `ase` \| `a2a` \| `aeh` \| `mcpchecker` \| `both` |
| `treatment_image_ref` | — | no | — | Image digest refs |
| `control_image_ref` | — | no | — | |
| `harbor_fork_revision` | — | no | — | |

Snapshot-only identity fields (not required on `Provenance`): `schema_version`,
`agent`, `role`, `slug`, `model`, `harness_path`, `skills`, `traceparent`.

Existing consumers that only set `commit_sha` / `pipeline_run_id` remain valid.

## Harness snapshot lifecycle

1. **Write (agent runtime):** At run start, after the root span exists, write
   `harness-snapshot.json` into the run output directory (next to Level-1
   telemetry). Fill forge/CI fields from process env / explicit overrides at
   write time. Mirror join keys on the root span attributes.
2. **Handoff (eval case output):** Opaque / skill runners copy the file to a
   stable path under the case or run output tree (same convention as other
   case artifacts such as metrics).
3. **Log (eval engine):** If the file is present when logging results, upload
   it as MLflow artifact **`harness-snapshot.json`** and project non-empty join
   fields to MLflow tags (names in the field table; use `commit_sha` for
   `ref_revision`).
4. **Read (later consumers):** Resolve experiment → MLflow run by
   `tags.mlflow.runName = '<eval_run_id>'` (or equivalent) →
   `download_artifacts(..., "harness-snapshot.json")`. Prefer tags when only
   scalars are needed. Do **not** treat ambient CI env as the source of truth
   when the artifact or tags exist.

## MLflow tag mapping (Phase C observer)

When `MLflowObserver` (or an eval engine logger) logs a run, prefer these
tag names (match Provenance fields):

`commit_sha`, `pipeline_run_id`, `repository_url`, `change_id`, `trace_id`,
`session_id`, `eval_run_id`, `harness_fingerprint`, `forge_platform`,
`eval_engine`.

Eval engines may also emit `ref_revision` alongside `commit_sha` when mirroring
snapshot JSON; prefer `commit_sha` for Provenance.

## Naming hygiene for downstream PRs

- Contract and ADR **Decision** text describe **roles** (agent runtime, eval
  engine MLflow logger), not other products’ module paths.
- Eval engine public docs must not claim ownership of this schema.
- Agent-runtime ADRs link here under Related; they do not redefine the field
  list.

## Scorecard

`Scorecard.provenance` remains a free-form `dict` for extras. Prefer copying
known keys from `AnalysisResult.provenance` so Compass/publish stay consistent.
