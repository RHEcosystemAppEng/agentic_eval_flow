# Controlled Forge evaluations

This extension uses AEH and Tekton in a configured namespace, with a new temporary
OpenShell sandbox for every case. It does not restart or update the live Forge
application. The source changes span this repository and the companion
`agent-eval-harness` checkout. The development trigger packages local source
into an immutable, SHA-256-verified ConfigMap. Source
commits alone do not describe an overlaid run: retain `overlay-manifest.json`.
It copies installed Tasks and adjusts their scripts; it is development tooling,
not the final CI interface. Moving Forge-owned content to `forge-eval` and
replacing overlays with pinned published dependencies are a separate phase.

## Evaluation tiers

1. `submissions/openclaw-forge-smoke`: a parent delegates one file read/write to
   the image's restricted `brief-reader`, waits, and consumes the result. A
   trace-backed gate requires successful child read/write and parent readback;
   a parent writing the marker itself cannot pass.
2. `submissions/openclaw-forge-controlled`: fixed sealed evidence, a fixed UTC
   clock, synthetic identity, and a prior empty brief. Expected IDs are
   host-only. The agent starts at fan-out and uses the image's composer and
   publisher. It has only the model provider attached.
3. `submissions/openclaw-forge-drafts`: eleven lifecycle cases using the real
   `/sandbox/bin/forge-draft` against a temporary HTTPS fixture on loopback.
   This fixture has no send route or provider credentials. It implements the
   agent fence's list/show/create/revise/withdraw subset, optimistic versions
   and exact-content idempotency. It is not a replacement for service contract
   testing or a test of the live drafts fence's network policy.
4. `submissions/openclaw-forge-connectors`: separate governed mock M365
   collection. Its variable retrieval latency and token counts are excluded
   from sealed skill comparisons. Slack integration remains an additional
   connector case to configure when its mock source is available.

Use the smoke tier after runtime changes, selected paired cases for a skill
change, and the complete lifecycle/connector suites before promotion. LLM
judges are off in these tiers: critical correctness is deterministic. If adding
tone/synthesis judges, give them the canonical artifact and its evidence, pin
model/rubric revisions, and report judge tokens separately.

## Prerequisites

- Authenticated `oc`, `git`, Python with PyYAML, and an AEH OpenShell pipeline
  already installed in the namespace. The trigger clones its Tasks under
  content-addressed names; the existing pipeline is preserved.
- The local harness checkout containing the Forge gateway, artifact, fixture,
  and usage changes. Use a Python virtual environment for tests.
- The namespace's current OpenShell mTLS Secret, Forge CA Secret, and synthetic
  USER.md Secret. No live USER.md is needed. Keep these as Secret references.
- Pipeline egress to GitHub, Python package endpoints and required registries,
  plus access to the gateway/model/storage services. Keep approved destinations
  in the Helm values because the namespace firewall is Helm-managed.
- A valid immutable agent image digest. For a private test image, authenticate
  and pre-pull that digest into the gateway VM's rootless Podman store using a
  temporary auth file. A pipeline pod pull Secret does not authenticate the VM
  gateway. Do not make a private derived image public to work around a pull.
- MLflow's service URL must include its actual port (`:5000` in this setup).
  The cleanup Task must use a valid pinned CLI image, not the nonexistent
  `registry.redhat.io/openshift4/ose-cli:latest`.

Keep the namespace's working PipelineRun JSON outside this checkout, and set
`BASE_RUN` to that file and `EVAL_NAMESPACE` to its namespace. Use the installed
pipeline's template from the [manual trigger guide](manual_trigger_guide.md)
to configure it; do not copy another namespace's service IPs or certificates.
The JSON must identify the installed `pipelineRef`, submission/source revisions,
gateway endpoint, image digest, workspace/storage configuration and Secret
references. Personal deployment snapshots are deliberately not versioned here.
The trigger clears the inline judge API-key parameter; use installed Secret
wiring for a suite that needs a judge key.
Gateway certificate rotation requires refreshing the eval mTLS Secret from the
currently selected gateway profile using the Agent VM SSH key. Do not print
certificate private keys or tokens into CI logs.

## Trigger

From this repository, with `HARNESS_CHECKOUT` set to the companion worktree:

```bash
python scripts/trigger_forge_controlled.py \
  --namespace "$EVAL_NAMESPACE" \
  --base-run "$BASE_RUN" \
  --harness "$HARNESS_CHECKOUT" \
  --submission submissions/openclaw-forge-smoke \
  --output /tmp/forge-smoke-deployment \
  --submit
```

For a briefing, select `submissions/openclaw-forge-controlled` and optionally
`--case decision-deadline`. Omitting `--case` runs the submission's full case
set. Omit `--submit` to render resources without creating a run. The command
resolves branch inputs to full commit IDs and records source-file hashes.
Git commands have bounded network waits and accept both branch and commit
references. Neither the image nor the live application is upgraded by this
command.

The configured pipeline must include Task wiring for its synthetic user and
Forge CA. On a new namespace, install/configure AEH first
using the existing deployment and manual trigger guides. This script is not a
cluster bootstrap installer.

## Execution and gates

`prepare` fetches pinned sources and validates the base submission. `evaluate`
checks the overlay checksum, stages its config/cases/harness, creates a fresh
sandbox, and consumes the image-owned profiles through an isolated loopback
OpenClaw gateway. `agent exec` alone lacks the published reply runtime needed
by child sessions. The supervisor stays alive when the parent yields and waits
for its final resumed response.

Required outputs are downloaded before the sandbox is deleted. Briefing cases
validate the actual `brief.json` with the image schema and deterministic
fixture/source checks, composer hash, coverage, publication time and disclosure
of unavailable/truncated evidence. Draft cases validate the service's in-memory before and
after snapshots and operation ledger. Expected answers never enter the agent
workspace. Host-produced results use `eval-results/` to avoid AEH's single-file
output-directory collision.

The report distinguishes `infrastructure_failed`, `invalid_eval`,
`quality_failed`, and `passed`. An exit-zero chat answer is insufficient.
Parent and recursively discovered child trajectories are retained. Unique
request response IDs reconcile against per-run aggregates, including resumed
parents. Missing/conflicting usage or truncated/error responses invalidate a
cost comparison. Reasoning tokens are reported as a subset of output, never
added a second time. Model connectivity probes are infrastructure overhead,
outside the measured agent task; their usage remains in the pipeline log.
These are reconciled exported-model usage measurements, not an independent
provider billing audit. Retries that a provider never exposes cannot be counted
from the trace; known incomplete/failed responses invalidate the measurement.

`analyze` builds the report. `store` uploads the AEH artifact tree and reads
every object back to verify its SHA-256. Failed uploads fail the Task. `gate`
runs after storage and requires a nonempty per-run `storage-attestation.json`,
case validity/usage, aggregate quality, and the
FINISHED MLflow run with summary artifacts. A bad skill can therefore produce
a failed final gate **and still retain its evidence**.
The Forge trigger explicitly sets `AEH_REQUIRE_ARTIFACTS=1` on its publisher.
This requires MinIO configuration, artifacts and successful read-back. Direct
publisher callers can use `--require-aeh-artifacts`. Other AEH users retain the
existing optional/best-effort upload behavior unless they opt in; that mode
does not produce a verified storage attestation and cannot pass the Forge gate.

PVC cleanup is allowed only after `store` succeeds. Failure before storage
keeps the claim for diagnosis. Completed Tekton pods can keep a delete-requested
PVC in `Terminating`; verify actual reclamation separately. Do not delete old
PipelineRuns or pods during installation. Retain experiment plans, overlays and
reports for at least 30 days; remove only explicitly selected completed eval
resources after verifying durable artifacts. No automatic historical pruning
is installed by this extension.

## A skill-only comparison

Freeze main and the candidate to full image-repository commit IDs, then prepare
the experiment before running either arm:

```bash
python scripts/prepare_forge_experiment.py \
  --submission submissions/openclaw-forge-drafts \
  --baseline "$BASELINE_COMMIT" --candidate "$CANDIDATE_COMMIT" \
  --case accepted-fresh-chat --pairs 3 --policy regression \
  --output /tmp/forge-draft-experiment

PYTHONPATH=. python scripts/run_forge_experiment.py \
  --experiment /tmp/forge-draft-experiment/experiment.json \
  --namespace "$EVAL_NAMESPACE" \
  --base-run "$BASE_RUN" \
  --harness "$HARNESS_CHECKOUT"
```

Make both skill commits accessible through the `rh-forge/openclaw-saw-image`
GitHub Contents API first (for example, a branch or PR commit; no merge is
required). This preparer reads from that repository, not uncommitted local files,
and has no separate fork selector. Set `BASELINE_COMMIT` and `CANDIDATE_COMMIT`
to full SHAs.
The preparer uses authenticated `gh api` to read exact skill bytes. Only the
selected skill is replaced; the launcher verifies its hash and records every
loaded skill/tool and workspace Markdown hash (including synthetic USER.md).
`--skill forge-drafts` (default) reads `tools/forge-draft/SKILL.md` and installs
it as `skills/forge-drafts/SKILL.md`. `--skill daily-briefing` reads the bundle's
daily-briefing skill. No image rebuild is needed for this skill-only comparison;
runtime/tool changes need a separately pinned candidate image integration run.
Both runtime overlays are frozen before execution.
Order alternates by pair; cases get fresh conversations and identical stored
state. The runner does not retry based on an unfavorable outcome. It refuses
to overwrite an experiment that has already started.

The comparison rejects missing/unplanned runs, mismatched image/model/prompt/
runtime/initial state, incomplete usage, and changes to other loaded files.
It also requires successful infrastructure/storage Tasks, a durable artifact
attestation and verified MLflow summaries. A baseline quality failure is valid
evidence; a failed upload is not.
Choose the policy before running either arm; it is frozen in `experiment.json`.
`regression` permits unchanged passing quality and cost. `improvement` (the default
for compatibility with earlier experiments) additionally requires a measured
correctness gain. Every candidate must pass critical checks under either policy,
even if the baseline failed them. Both require no observed per-case mean output increase and
non-increasing aggregate mean/p95 output. A one-sided paired bootstrap upper
bound must also be <= 0. Three pairs are a pilot; uncertainty crossing zero is
`inconclusive`. Declare a larger experiment (maximum ten pairs) before running
it; do not add repetitions until a favorable sample appears.

The experiment runner exits with the comparison result: a valid improvement or
regression-policy pass exits zero; invalid, inconclusive, or failing comparisons
exit nonzero. Inspect `comparison.json` and its linked run evidence before editing
the skill and declaring a new experiment. Do not edit an in-progress experiment.
No skill is promoted, pushed, or merged automatically.

## Investigating a run

Read `prepare`/`evaluate` logs for infrastructure errors, then inspect
`run_result.json`, `provenance.json`, `request-usage.json`, the deterministic
verdict, and the canonical artifact. Model trace contents are for diagnosis;
the published artifact and stored state decide correctness.

If the PVC remains, fetch reports with a temporary read-only reader pod:

```bash
python scripts/fetch_forge_reports.py --namespace "$EVAL_NAMESPACE" \
  --run "$PIPELINE_RUN" --output /tmp/forge-eval-results
```

The reader pod is deleted after copying. It does not change the PVC. When the
claim has already been reclaimed, retrieve the same report tree from the
verified MinIO `debug/aeh/` prefix recorded by the store Task instead.

For one explicitly selected completed run, archive logs and release its already
delete-requested PVC after fetching the storage attestation:

```bash
python scripts/cleanup_forge_run.py --namespace "$EVAL_NAMESPACE" \
  --run "$PIPELINE_RUN" --report "$LOCAL_HARNESS_RUN_DIRECTORY" \
  --archive "$PRIVATE_LOG_ARCHIVE"
```

This removes only that run's completed pods, retaining PipelineRun/TaskRun
records and archived logs. It verifies actual claim deletion. It refuses a
running run, unverified storage, or a PVC without a prior deletion request.
