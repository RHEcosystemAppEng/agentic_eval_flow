# Agentic Eval Flow

`abevalflow` validates skills and runs Agent Eval Harness (AEH) evaluations through
Harbor on Kubernetes/OpenShift. Install the `abeval` CLI locally or in a container.
The same application services power combined runs and independent container phases.

## Install

Python 3.12 or newer is required. From a checkout:

```sh
uv tool install .
abeval --version
```

Install the optional capabilities you need:

```sh
uv tool install '.[llm,runner,storage,observability]'
```

| Extra | Capability |
|---|---|
| `llm` | Advisory skill and submission reviews |
| `runner` | Pinned Harbor, Kubernetes client, and evaluation model clients |
| `storage` | PostgreSQL and S3-compatible storage clients |
| `observability` | OpenTelemetry OTLP tracing |
| `all` | All runtime capabilities |
| `dev` | Tests, coverage, lint, and distribution build tools |

The base installation can validate structure, validate submissions, and plan runs
without model credentials, a database, or Harbor. Full Tier 1 needs scanner
executables on PATH or Podman/Docker with the scanner images. Scanners and the
trial agent runtime are separate from Python extras.

## External AEH runtime

Tier 2 invokes the complete, unchanged Agent Eval Harness v1.57.1 distribution.
Install it once in an isolated environment:

```sh
abeval runtime install
abeval runtime check
```

The installer needs Git, network access, and a Python >=3.12 interpreter with
venv/pip support. It pins AEH to commit
`8a15a8b687b5561903827805eaae8b438bde9954` and Harbor to `0.24.0`. The default
installation lives under `$XDG_CACHE_HOME/abeval/aeh/<commit>` (or
`~/.cache/abeval/aeh/<commit>`). Dependency resolution follows upstream's declared
requirements with the supported Harbor pin. Existing directories are never
replaced; failed installations are removed so installation can be retried.

For a different installation directory:

```sh
abeval runtime install --directory /path/to/aeh-runtime --python /path/to/python3.12
export ABEVAL_AEH_ROOT=/path/to/aeh-runtime/source
export ABEVAL_AEH_PYTHON=/path/to/aeh-runtime/.venv/bin/python
abeval runtime check
```

An existing complete upstream source installation can also be selected with
`--aeh-root` and `--aeh-python` on workflow commands. Python paths retain their
virtual-environment identity. The runtime must include upstream's `skills/` and
templates; a wheel-only AEH installation is insufficient for this release.
The application calls upstream task generation, execution, scoring, and report
commands without changing AEH code or overriding its functions. Runner/trial
images install the complete source distribution during their build.

## Two tiers

**Tier 1** validates structure and runs deterministic quality and security scans.
`--skill-review` additionally requests an advisory LLM review of `SKILL.md`.

**Tier 2** validates an AEH submission, reviews its cases and judges unless
`skip_quality_review: true`, runs single or pairwise evaluation through Harbor,
and aggregates results. Submission review is separate from agent execution and
from the judges configured in evaluation YAML.

```sh
abeval validate skill ./pack/skills/my-skill
abeval validate submission ./submissions/pack/my-skill

# Use the pinned scanner containers without installing scanner Python packages.
abeval run ./pack/skills/my-skill --tiers 1 --scanner-runtime podman

# Native scanners must provide skill-scanner and harness-eval on PATH.
abeval run ./pack/skills/my-skill --tiers 1

# Execute static checks followed by live evaluation.
abeval run ./pack/skills/my-skill --tiers 1,2 \
  --submission ./submissions/pack/my-skill --trial-image quay.io/example/trial@sha256:...
```

Tier 1 is the default. Available credentials never enable model calls or
publication by themselves. A blocking Tier 1 failure skips Tier 2 while preserving
reports. `--continue-on-tier1-failure` requests evaluation despite those findings
and retains the failed Tier 1 verdict. Required scanner failures are runtime
errors, not clean scans. Submission-review findings are advisory unless the
submission's quality gate is blocking.

For the checked-in example:

```sh
abeval run submissions/aeh-pairwise-example/skills/aeh-pairwise-example \
  --source . --tiers 1 --scanner-runtime podman
```

## Configuration

Every runtime option has an `ABEVAL_` environment equivalent: replace hyphens
with underscores and uppercase the name. Flags override their corresponding
environment variables. Evaluation definitions, cases, and judges remain in
submission files. The CLI discovers `submissions/<pack>/<skill>` for the standard
`<pack>/skills/<skill>` layout; use `--submission` for other layouts.

```sh
export ABEVAL_TIERS=1,2
export ABEVAL_SCANNER_RUNTIME=podman
export ABEVAL_NAMESPACE=agentic-plugins-evaluation
export ABEVAL_TRIAL_IMAGE=quay.io/example/trial@sha256:...
abeval plan ./pack/skills/my-skill
```

`plan` shows the selected operations, non-secret effective configuration, and
configuration origins. It does not stage source, contact services, or run models.
`doctor` checks the requested dependencies and workload-cluster permissions;
`doctor --offline` checks only local prerequisites. Readiness checks do not
invoke models or create trial pods.

| Purpose | Environment variables |
|---|---|
| Review API | `ABEVAL_LLM_BASE_URL`, `ABEVAL_LLM_MODEL`, `ABEVAL_LLM_API_KEY` |
| Agent API | `ABEVAL_AGENT_BASE_URL`, `ABEVAL_AGENT_API_KEY` |
| Suite-level judge API | `ABEVAL_JUDGE_BASE_URL`, `ABEVAL_JUDGE_API_KEY` |
| AEH runtime | `ABEVAL_AEH_ROOT`, `ABEVAL_AEH_PYTHON` |
| Cluster | `ABEVAL_KUBECONFIG` / `KUBECONFIG`, `ABEVAL_KUBE_CONTEXT`, `ABEVAL_NAMESPACE` |
| Workload model Secret | `ABEVAL_CREDENTIALS_SECRET` (default `llm-credentials`) |
| PostgreSQL | `ABEVAL_DATABASE_URL` |
| S3 | `ABEVAL_S3_ENDPOINT`, `ABEVAL_S3_ACCESS_KEY`, `ABEVAL_S3_SECRET_KEY`, `ABEVAL_S3_BUCKET` |
| Compass | `ABEVAL_COMPASS_TOKEN`; destination and fact identities remain in metadata |
| OCI | `ABEVAL_OCI_SUBJECT`, `ABEVAL_REGISTRY_CONFIG` |
| Tracing | `OTEL_EXPORTER_OTLP_ENDPOINT` |

Existing `LLM_*`, `ANTHROPIC_*`, `OPENAI_*`, `AGENT_EVAL_K8S_*`, `DATABASE_URL`,
and `MINIO_*` variables are accepted as aliases where applicable. Namespaced
variables take precedence over aliases. `MINIO_INSECURE_SSL=1` is supported;
`ABEVAL_S3_VERIFY_TLS=false` is the equivalent explicit setting.

`--submission-review auto` honors `skip_quality_review`; `on` and `off` are explicit
overrides. Disabling review conflicts with a blocking submission quality policy.
`aeh_mode`, control/treatment config paths, model definitions, gate thresholds,
and certification policies retain their submission-defined meanings.

## Local orchestration with remote trials

The CLI runs on your laptop; trial pods run on the selected workload cluster.

```sh
export KUBECONFIG=/path/to/workload.kubeconfig
export ABEVAL_LLM_BASE_URL=https://gateway.example/v1
export ABEVAL_LLM_MODEL=review-model
export ABEVAL_LLM_API_KEY='...'

abeval doctor ./pack/skills/my-skill --tiers 1,2 \
  --scanner-runtime podman --namespace agentic-plugins-evaluation \
  --trial-image quay.io/example/trial@sha256:...

abeval run ./pack/skills/my-skill --tiers 1,2 \
  --scanner-runtime podman --namespace agentic-plugins-evaluation \
  --trial-image quay.io/example/trial@sha256:...
```

An explicit kubeconfig overrides in-cluster credential discovery. `--kube-context`
selects a context without editing the user's kubeconfig. Without an explicit
namespace, the kubeconfig context or in-cluster namespace is used, then `default`.
The account needs pod creation, inspection, deletion, log access, and exec access.
The remote namespace must contain the configured workload credentials Secret.
Local API credentials do not automatically populate remote Secrets.

The workload cluster must pull the trial image and reach the model/tool endpoints.
The local orchestrator must reach the Kubernetes API and any review or suite-level
judge endpoints. AEH's native Kubernetes environment manages trial pods; the trial
image supplies writable group-0 workspace and verifier directories.

The workload Secret must use native SDK variable names, including
`ANTHROPIC_API_KEY` for Claude and the corresponding judge provider's key.
A Secret containing only `api-key` must be migrated. Configure internal CA trust
through standard SDK/OS settings, including `SSL_CERT_FILE` and
`NODE_EXTRA_CA_CERTS` in the trial environment when needed. The application does
not install HTTP-client overrides or disable TLS verification.

## Reports and persistence

A run creates an isolated source copy and saves evidence under
`.abeval/runs/<run-id>/` by default. `--output` selects a new directory. Existing
run directories are not overwritten, and concurrent writers are rejected.
Reports are produced independently of publication and retained after failures.

| Artifact | Contents |
|---|---|
| `run.json` | Versioned configuration, source identity, step outcomes, artifact paths, publication results |
| `reports/scorecard.json`, `scorecard.md` | Gate results and policy recommendation |
| `reports/certification.json` | Certification levels and checks |
| `reports/report.json`, `report.md` | AEH results when evaluation ran |
| Scanner JSON/SARIF and logs | Static evidence, including failed scans |
| `reports/_ai_review.json` | Submission review and usage metrics when requested |

Execution status, policy verdict, and publication status are separate. A failed
upload does not change the skill's evaluation verdict. Selected persistence
failures fail the command by default; `--no-persistence-required` makes them
advisory. Each destination is attempted independently, including on failed runs.

```sh
export ABEVAL_DATABASE_URL='postgresql+psycopg://...'
export ABEVAL_S3_ENDPOINT=https://minio.example
export ABEVAL_S3_ACCESS_KEY='...'
export ABEVAL_S3_SECRET_KEY='...'

abeval run ./pack/skills/my-skill --tiers 1 --scanner-runtime podman --persist postgres,s3
abeval publish --run-dir .abeval/runs/<run-id> --persist postgres,s3
abeval report --run-dir .abeval/runs/<run-id>
```

PostgreSQL stores evaluation rows, trials, scans, scorecards, certification, and
review metrics. Retries complete missing records without duplicating a run.
S3 stores reports and the run manifest under `<skill>/<run-id>/`; source copies
and kubeconfigs are excluded. Compass publishes the facts selected in metadata.
OCI publication requires `--oci-subject repository@sha256:<digest>` and ORAS on
PATH; reports are attached as `application/vnd.agentic-eval.reports.v1`.
Credentials are excluded from serialized runtime configuration.

The CLI writes structured JSON to stdout and execution diagnostics to stderr.

Exit codes: `0` success; `1` blocking findings; `2` invalid invocation or
configuration; `3` required runtime or publication failure. Advisory findings
remain visible in reports without failing the command.

## Container phases

Initialize a run once, then invoke its declared phases from individual images:

```sh
abeval init ./pack/skills/my-skill --tiers 1 --output /workspace/run
abeval phase prepare --run-dir /workspace/run
abeval phase structure --run-dir /workspace/run
abeval phase security --run-dir /workspace/run
abeval phase quality --run-dir /workspace/run
abeval phase report --run-dir /workspace/run
```

All phase containers must mount the run and authored source at the manifest's
absolute paths. Runtime credentials are read from the current environment.
A phase with existing evidence is not rerun implicitly. Publishing can be retried
without repeating checks or trials. Existing `scripts/*.py` files are compatibility
launchers for deployed task bundles; application behavior resides in the package.

## Development

```sh
uv sync --locked --extra dev --extra observability
make test
uv build
```

See [architecture](Docs/architecture.md), [submission formats](Docs/submission-formats.md),
[image builds](Docs/images.md), [persistence](Docs/persistence.md), and
[Konflux integration](Docs/konflux-integration-guide.md).
