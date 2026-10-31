# CLI and environment reference

Start with [installation](../README.md#install). Run the examples from your skill
catalog root. Tier 1 runs entirely locally with the included structure, security,
and quality checks. No images, cluster, or model credentials are needed unless
you enable optional capabilities. Tier 2 runs AEH trials on Kubernetes.

```sh
# Full Tier 1 with the scanners included in the CLI installation.
abeval run ./pack/skills/my-skill --source . --tiers 1

# Inspect configuration before running both tiers.
abeval plan ./pack/skills/my-skill --source . --tiers 1,2
abeval doctor ./pack/skills/my-skill --source . --tiers 1,2

# Publish an existing run without repeating evaluation.
abeval publish --run-dir .abeval/runs/<run-id> --persist postgres,s3
```

Tier 2 also needs an [AEH runtime](../README.md#external-aeh-runtime), a pullable
trial image, cluster access, and a workload model Secret. See
[local orchestration with remote trials](../README.md#local-orchestration-with-remote-trials)
for setup and [submission formats](submission-formats.md) for cases, judges,
models, thresholds, and MCP fixtures.

## Commands

| Command | Purpose and command-specific options |
|---|---|
| `run [skill]` | Execute selected tiers and publish when requested. |
| `plan [skill]` | Show resolved non-secret configuration and operations; no execution. |
| `doctor [skill]` | Check dependencies and cluster permissions; `--offline` skips cluster access. No models or trial pods are started. |
| `init [skill]` | Create a run manifest for separate container phases. |
| `validate skill PATH` / `validate submission PATH` | Validate without model calls; `--output FILE` optionally saves JSON. |
| `runtime install` | Install pinned AEH; `--directory DIR` chooses a new location, `--python PATH` chooses Python >=3.12. Defaults: user cache and the CLI's interpreter. |
| `runtime check` | Check an installation; accepts `--aeh-root` and `--aeh-python` or their environment variables below. |
| `phase NAME --run-dir DIR` | Execute one selected phase once; `--if-selected` skips a phase absent from the saved plan. Names: `prepare`, `structure`, `security`, `quality`, `skill-review`, `submission`, `submission-review`, `evaluate`, `report`. |
| `report --run-dir DIR` | Regenerate reports from saved evidence. |
| `publish --run-dir DIR` | Publish saved evidence; accepts `--persist`, `--database-url`, `--s3-endpoint`, `--s3-bucket`, and `--[no-]persistence-required`. |

Global options are optional: `--help` / `-h`, `--version`, and `--verbose`
(before the command). Every command also accepts `--help`.
Command-specific options such as `--run-dir`,
`--offline`, `runtime install --directory`, and `validate --output` have no
`ABEVAL_` environment equivalent.

### Command-specific arguments

| Command | Argument / flag | Required? |
|---|---|---|
| `doctor` | `--offline` | Optional; skip cluster checks. |
| `validate` | `skill` / `submission`, `PATH` | Required. |
| `validate` | `--output` | Optional. |
| `runtime install` | `--directory`, `--python` | Optional. |
| `runtime check` | `--aeh-root`, `--aeh-python` | Optional; installation is auto-discovered. |
| `phase` | `NAME`, `--run-dir` | Required. |
| `phase` | `--if-selected` | Optional. |
| `report`, `publish` | `--run-dir` | Required. |
| `publish` | `--persist`, `--database-url`, `--s3-endpoint`, `--s3-bucket`, `--[no-]persistence-required` | Optional overrides of the saved run. Selected destinations still require their configuration below. |

## Configuration rules

The tables below apply to **`run`, `plan`, `doctor`, and `init`**. Explicit flags
win over `ABEVAL_*` variables, followed by the listed aliases in order, then the
default. Empty environment values are treated as unset. There is no automatic
`.env` loading by the CLI; supply variables in its process environment.

Boolean flags use `--name` / `--no-name`; their environment values accept
`true/false`, `1/0`, `yes/no`, or `on/off`. For example,
`--no-skill-review` overrides `ABEVAL_SKILL_REVIEW=true`.
Credentials alone do not enable optional reviews or publication.

**Required?** describes whether you must supply a setting. A required setting
can come from its flag or environment variable. “When …” means required only for
that capability. Defaults and submission discovery make most flags optional.

### Run selection

| Flag / argument | Environment variable | Default / meaning | Required? |
|---|---|---|---|
| `skill` (positional) | `ABEVAL_SKILL` | Current directory; must contain `SKILL.md`. | Optional. |
| `--skill-id` | `ABEVAL_SKILL_ID` | Alternative to a skill path: `<pack>/<skill>` resolves under `--source/<pack>/skills/<skill>`. | Optional. |
| `--source` | `ABEVAL_SOURCE` | Nearest enclosing Git root, otherwise the skill directory. Must contain the skill and submission. | Optional. |
| `--submission` | `ABEVAL_SUBMISSION` | Discover `submissions/<pack>/<skill>` for `<pack>/skills/<skill>`; otherwise supply a path. | When Tier 2 cannot discover the submission. |
| `--tiers` | `ABEVAL_TIERS` | `1`; choices: `1`, `2`, `1,2` (sequential). | Optional. |
| `--output` | `ABEVAL_OUTPUT` | `.abeval/runs/<run-id>` under the current directory; must be a new directory outside the skill and submission. | Optional. |
| `--run-id` | `ABEVAL_RUN_ID` | Generated UTC timestamp plus random suffix; explicit IDs use letters, digits, `.`, `_`, `-`, start with a letter/digit, and are at most 101 characters. | Optional. |
| `--revision` | `ABEVAL_REVISION` | `unknown`; source revision recorded in reports. PostgreSQL requires a full commit SHA. | When publishing to PostgreSQL. |
| `--plugin-name` | `ABEVAL_PLUGIN_NAME` | Inferred from the catalog path or submission `plugin_name`. | For PostgreSQL when no plugin can be inferred. |
| `--built-image-ref` | `ABEVAL_BUILT_IMAGE_REF` | Evaluated content image as `repository@sha256:<digest>`; separate from the trial image. | When a built content image is available. |
| `--[no-]continue-on-tier1-failure` | `ABEVAL_CONTINUE_ON_TIER1_FAILURE` | `false`; continue to Tier 2 while retaining Tier 1 failures. | Optional. |
| `--command-timeout` | `ABEVAL_COMMAND_TIMEOUT` | `7200` seconds per external command; positive integer. | Optional. |

### Tier 1 scanners

| Flag | Environment variable | Default / meaning | Required? |
|---|---|---|---|
| `--scanner-runtime` | `ABEVAL_SCANNER_RUNTIME` | `local`; choices: `local`, `podman`, `docker`. | Optional. |
| `--security-command` | `ABEVAL_SECURITY_COMMAND` | Included `skill-scanner`; override with an executable path/name. | Optional. |
| `--quality-command` | `ABEVAL_QUALITY_COMMAND` | Included `harness-eval`; override with an executable path/name. | Optional. |
| `--security-image` | `ABEVAL_SECURITY_IMAGE` | No default; image containing `skill-scanner`. | When Tier 1 uses Podman/Docker. |
| `--quality-image` | `ABEVAL_QUALITY_IMAGE` | No default; image containing `harness-eval`. | When Tier 1 uses Podman/Docker. |

Local execution is the default and uses the scanners installed alongside
`abeval`, including with `uv tool install`; scanner commands do not need to be
exposed on your shell's PATH. Command overrides select custom executables.

<details>
<summary>Optional Podman/Docker scanning</summary>

Choose this mode when you want container isolation. Both image flags can select
the same published evaluation image, preferably pinned by digest:

```sh
abeval run ./pack/skills/my-skill --scanner-runtime podman \
  --security-image quay.io/example/agent-eval-harness:v5.0-runner \
  --quality-image quay.io/example/agent-eval-harness:v5.0-runner
```

</details>

### Models and reviews

| Flag | Environment variable | Default / meaning | Required? |
|---|---|---|---|
| `--[no-]skill-review` | `ABEVAL_SKILL_REVIEW` | `false`; add advisory LLM review of `SKILL.md`. | Optional. |
| `--submission-review` | `ABEVAL_SUBMISSION_REVIEW` | `auto`; choices: `auto`, `on`, `off`. Auto honors `metadata.yaml`'s `skip_quality_review` in Tier 2. A blocking quality policy requires review. | Optional. |
| `--llm-base-url` | `ABEVAL_LLM_BASE_URL` | Review API URL; required when a review is selected. | When a review is enabled. |
| `--llm-model` | `ABEVAL_LLM_MODEL` | Review model; required when a review is selected. | When a review is enabled. |
| `--llm-api-key` | `ABEVAL_LLM_API_KEY` | Review credential; required when a review is selected. | When a review is enabled. |
| `--model` | `ABEVAL_MODEL` | Use the submission's `models.skill` unless overridden. | Optional. |
| `--judge-model` | `ABEVAL_JUDGE_MODEL` | Use the submission's `models.judge` unless overridden. | Optional. |
| `--agent-base-url` | `ABEVAL_AGENT_BASE_URL` | Agent endpoint passed to AEH; otherwise provider configuration applies. | Optional. |
| `--agent-api-key` | `ABEVAL_AGENT_API_KEY` | Local orchestration credential passed to AEH; does not create a workload Secret. | Optional. |
| `--judge-base-url` | `ABEVAL_JUDGE_BASE_URL` | Suite-level judge endpoint, including pairwise comparison. | Optional. |
| `--judge-api-key` | `ABEVAL_JUDGE_API_KEY` | Local suite-level judge credential. | Optional. |
| `--[no-]insecure-ssl` | `ABEVAL_INSECURE_SSL` | `false`; opt in to the legacy TLS bypass for evaluation HTTP clients, Compass publishing, and the database route. Kubernetes API TLS stays verified; S3 has its own flag. | Optional. |

Review, trial execution, and evaluation judging are separate model operations.
Tier 2 agents and judges need their provider credentials; the endpoint/key flags
are optional overrides of provider configuration and workload Secret settings.
For an OpenAI-compatible gateway, review and suite-level judge URLs normally end
in `/v1`; Claude's Anthropic-compatible agent URL normally uses the gateway root.
Use environment variables for credentials so they do not appear in command history.

### Tier 2 workload and AEH

| Flag | Environment variable | Default / meaning | Required? |
|---|---|---|---|
| `--backend` | `ABEVAL_BACKEND` | `kubernetes` (only supported backend). | Optional. |
| `--kubeconfig` | `ABEVAL_KUBECONFIG` | Explicit workload kubeconfig; otherwise in-cluster credentials or the default kubeconfig. | Optional. |
| `--kube-context` | `ABEVAL_KUBE_CONTEXT` | Selected kubeconfig's current context unless overridden; does not change the user's config. | Optional. |
| `--namespace` | `ABEVAL_NAMESPACE` | Kubeconfig context namespace or in-cluster namespace, then `default`. | Optional. |
| `--credentials-secret` | `ABEVAL_CREDENTIALS_SECRET` | `llm-credentials`; an existing Secret in the workload namespace. | Optional. |
| `--trial-image` | `ABEVAL_TRIAL_IMAGE` | No default; supply a cluster-pullable trial image, preferably pinned by digest. | When Tier 2 is selected. |
| `--n-concurrent` | `ABEVAL_N_CONCURRENT` | `1`; maximum AEH trial cases running concurrently. Must be at least `1`; cluster capacity should match the requested concurrency. | Optional. |
| `--aeh-root` | `ABEVAL_AEH_ROOT` | Complete AEH source installation; discover an installed source distribution or the managed cache. | Optional. |
| `--aeh-python` | `ABEVAL_AEH_PYTHON` | Installation's sibling `.venv/bin/python` if present, otherwise the CLI's interpreter. | Optional. |
| `--score-script` | `ABEVAL_SCORE_SCRIPT` | External AEH installation's `score.py`; override for pairwise aggregation. | Optional. |

The workload Secret uses native SDK names, such as `ANTHROPIC_API_KEY` and
`OPENAI_API_KEY`, plus endpoint settings as needed. Local credentials are not
copied into it. Mock server configuration comes from submission fixtures;
only fixture-backed MCP servers are registered in trials.

### Persistence (opt-in)

| Flag | Environment variable | Default / meaning | Required? |
|---|---|---|---|
| `--persist` | `ABEVAL_PERSIST` | Disabled; comma-separated `postgres,s3,compass`. | Optional. |
| `--[no-]persistence-required` | `ABEVAL_PERSISTENCE_REQUIRED` | `true`; selected publication failures fail the command. | Optional. |
| `--database-url` | `ABEVAL_DATABASE_URL` | PostgreSQL URL, e.g. `postgresql+psycopg://user:password@host/db`. | When `postgres` is selected. |
| `--database-tls-route-host` | `ABEVAL_DATABASE_TLS_ROUTE_HOST` | Optional verified PostgreSQL TLS passthrough route on port 443; database URL must use loopback. | Optional. |
| `--s3-endpoint` | `ABEVAL_S3_ENDPOINT` | S3/MinIO endpoint URL. | When `s3` is selected. |
| `--s3-bucket` | `ABEVAL_S3_BUCKET` | `ab-eval-reports`. | Optional. |
| `--s3-access-key` | `ABEVAL_S3_ACCESS_KEY` | S3 credential. | When `s3` is selected. |
| `--s3-secret-key` | `ABEVAL_S3_SECRET_KEY` | S3 credential. | When `s3` is selected. |
| `--[no-]s3-verify-tls` | `ABEVAL_S3_VERIFY_TLS` | `true`; verify S3 TLS certificates. | Optional. |
| `--compass-token` | `ABEVAL_COMPASS_TOKEN` | Compass bearer token; endpoint and fact identities come from `gate_policy.push_facts` in metadata. | When `compass` is selected and metadata has no token. |

See [persistence](persistence.md) for stored artifacts and retry behavior.

<details>
<summary>Existing environment aliases</summary>

These aliases apply to workflow commands when the corresponding flag and
`ABEVAL_*` variable are unset. Where multiple aliases are listed, the first set
value wins.

| Setting | Aliases (in precedence order) |
|---|---|
| `ABEVAL_REVISION` | `SUBMISSION_REVISION` |
| `ABEVAL_KUBECONFIG` | `KUBECONFIG` |
| `ABEVAL_NAMESPACE` | `AGENT_EVAL_K8S_NAMESPACE` |
| `ABEVAL_CREDENTIALS_SECRET` | `AGENT_EVAL_K8S_CREDENTIALS_SECRET` |
| `ABEVAL_TRIAL_IMAGE` | `AEH_IMAGE` |
| `ABEVAL_SCORE_SCRIPT` | `AEH_SCORE_PY` |
| `ABEVAL_LLM_BASE_URL` | `LLM_BASE_URL`, `LLM_API_BASE` |
| `ABEVAL_LLM_MODEL` | `LLM_MODEL` |
| `ABEVAL_LLM_API_KEY` | `LLM_API_KEY` |
| `ABEVAL_AGENT_BASE_URL` | `ANTHROPIC_BASE_URL` |
| `ABEVAL_AGENT_API_KEY` | `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` |
| `ABEVAL_JUDGE_BASE_URL` | `OPENAI_BASE_URL` |
| `ABEVAL_JUDGE_API_KEY` | `OPENAI_API_KEY` |
| `ABEVAL_INSECURE_SSL` | `AEH_INSECURE_SSL` |
| `ABEVAL_DATABASE_URL` | `DATABASE_URL` |
| `ABEVAL_DATABASE_TLS_ROUTE_HOST` | `DB_TLS_ROUTE_HOST` |
| `ABEVAL_S3_ENDPOINT` | `MINIO_ENDPOINT` |
| `ABEVAL_S3_ACCESS_KEY` | `MINIO_ACCESS_KEY`, `AWS_ACCESS_KEY_ID` |
| `ABEVAL_S3_SECRET_KEY` | `MINIO_SECRET_KEY`, `AWS_SECRET_ACCESS_KEY` |
| `ABEVAL_COMPASS_TOKEN` | `COMPASS_API_TOKEN` |

`MINIO_INSECURE_SSL=true` means `ABEVAL_S3_VERIFY_TLS=false`. An explicit
`--[no-]s3-verify-tls` or `ABEVAL_S3_VERIFY_TLS` takes precedence for new runs.

</details>

<details>
<summary>Environment-only settings and upstream configuration</summary>

| Variable | Purpose |
|---|---|
| `XDG_CACHE_HOME` | Parent of the managed AEH cache; defaults to `~/.cache`. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Enable OTLP/gRPC tracing for `run`, `phase`, `report`, and `publish`; install the `observability` extra. |
| `OTEL_SERVICE_NAME` | Trace service name; default `abevalflow`. |
| `AGENT_EVAL_K8S_SERVICE_ACCOUNT` | Native AEH trial service account; useful for image-pull credentials. No CLI flag. |
| `SSL_CERT_FILE` | Trusted CA bundle for Python clients; must exist in each process/container using it. |
| `NODE_EXTRA_CA_CERTS` | Additional CA bundle for the trial's Node/Claude process. |

Other AEH/provider settings belong to the installed upstream runtime. The CLI
passes the orchestrator environment to AEH; this does not copy all host variables
into trial pods. Supply pod credentials and CA paths through the workload Secret
and image/task inputs. The CLI sets `AGENT_EVAL_RUNS_DIR` for report collection
and `AGENT_EVAL_K8S_KEEP_RUN=0` for trial cleanup; these are not user overrides.
Kubernetes service discovery variables are supplied by Kubernetes automatically.

</details>

## Existing runs and exit codes

`phase`, `report`, and `publish` load saved configuration from `--run-dir`.
They refresh credentials from the current environment. They also accept current
`ABEVAL_DATABASE_TLS_ROUTE_HOST` / `DB_TLS_ROUTE_HOST`,
`ABEVAL_S3_ENDPOINT` / `MINIO_ENDPOINT`, `ABEVAL_S3_BUCKET`,
`ABEVAL_S3_VERIFY_TLS` / `MINIO_INSECURE_SSL`, and
`ABEVAL_PERSISTENCE_REQUIRED` settings.
Other workflow options remain as recorded. For `publish`, its explicit flags
listed above take precedence; `--persist` overrides saved destinations
(`ABEVAL_PERSIST` does not). It has no credential flags or `--s3-verify-tls` flag;
use the environment for those settings. `--run-dir` always requires a flag.

JSON results go to stdout; diagnostics go to stderr. Exit codes describe the
whole run, so a successful publication retry can still return a failure code
from earlier scan or evaluation evidence.

| Code | Meaning |
|---|---|
| `0` | Success. |
| `1` | Blocking policy findings. |
| `2` | Invalid invocation or configuration. |
| `3` | Required runtime or publication failure. |

## Sequential batch commands (0.4.0)

`abeval batch` accepts the same runtime, tier, review, and persistence flags as
`abeval run`, plus required `--skill-ids` followed by zero or more unique catalog
IDs. Do not combine it with a positional skill, `--skill-id`, or `--submission`.
Each skill discovers its own submission and calls the existing workflow once.
`--timeout-retries` / `ABEVAL_TIMEOUT_RETRIES` selects 0–5 additional AEH arm
attempts after a command or native trial timeout (default 1). Other failures are
not retried. `--command-timeout` remains the per-external-command deadline.
Retry evidence is retained; scans/reviews/reporting/persistence are not repeated.
Unconfirmed workload cleanup stops the batch rather than starting more trials.

```sh
abeval batch --source . --tiers 1,2 --persist postgres,s3 \
  --output /workspace/batch --skill-ids rh-sre/cve-validation rh-sre/cve-impact
abeval batch-package --batch-dir /workspace/batch
abeval batch-results --batch-dir /workspace/batch --revision "$REVISION" \
  --tiers 1,2 --persist postgres,s3 --results-dir /workspace/results \
  --skill-ids rh-sre/cve-validation rh-sre/cve-impact
```

`batch-package` archives batch.json plus every available skill's run.json and
reports. `batch-results` validates revision, selection, completed child evidence,
tiers, and persistence destinations, writes `summary.json`, and emits standard
Konflux result files. It gates Tier 1 and required publication; Tier 2 remains
informational. Missing or malformed selected evidence is ERROR. Large per-skill
outcomes stay in the summary, not Tekton result arrays. Registry attachment is
performed by Konflux and supplied as an attachment receipt. Report-only consumers
may select `--fail-on-non-success false`; the default is true.

Batch exit codes are 0 (all workflows passed), 1 (completed policy failure),
2 (invalid batch options), 3 (runtime/item failure), or 130 (interruption).
The shared task finalizes evidence after nonzero batch exit and applies its
separate Tier 1/publication gates. Configuration errors are isolated per skill.
