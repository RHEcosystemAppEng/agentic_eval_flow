# Konflux integration

Use the installed `abeval` CLI from a task or runner image. The
[CLI reference](cli.md) lists flags, environment variables, defaults, and exit
codes. Submission files define cases, models, judges, and policy.

## Ownership

| Konflux task | Evaluation CLI |
|---|---|
| Retrieve trusted source artifacts and select changed skills. | Stage an isolated source copy. |
| Pass changed skill IDs to one batch invocation. | Sequence existing single-skill workflows and retry timed-out AEH arms. |
| Mount kubeconfig, inject credentials, and provide image references. | Validate inputs, run scans/reviews, and orchestrate remote AEH trials. |
| Provide a shared workspace across task steps. | Record phase evidence and generate reports in a run directory. |
| Convert run evidence to Tekton results and attach OCI evidence. | Return structured JSON and documented exit codes. |
| Select publication destinations. | Publish to PostgreSQL, S3, or Compass when requested. |

## One-command execution

Run from the trusted catalog root. Supply runtime configuration through task
parameters and Secret-backed environment variables, then invoke:

```sh
abeval run ./pack/skills/my-skill --source . \
  --tiers 1,2 --scanner-runtime local \
  --run-id "$PIPELINE_RUN_ID" --output /workspace/evaluation \
  --persist postgres,s3
```

The CLI installation includes both scanners; `--scanner-runtime local` is the
default locally and in the evaluation image. OCI attachment and result projection
use existing upstream CI images.
The run ID must satisfy the CLI's identifier rules, and the output directory
must not already exist.

Provide an installed AEH runtime and `ABEVAL_TRIAL_IMAGE` for Tier 2. Mount the
workload kubeconfig and select its namespace with `ABEVAL_KUBECONFIG` and
`ABEVAL_NAMESPACE`. `ABEVAL_CREDENTIALS_SECRET` identifies an existing workload
Secret with native SDK keys such as `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`.
A Secret containing only `api-key` does not supply those names automatically.
Local review and pairwise judge credentials are separate from pod credentials;
configure them using the [model settings](cli.md#models-and-reviews).

## Separate container phases

For integrations that invoke individual phases in separate containers, use a
shared run directory:

```sh
abeval init ./pack/skills/my-skill --source . --tiers 1,2 \
  --run-id "$PIPELINE_RUN_ID" --output /workspace/evaluation
abeval phase prepare --run-dir /workspace/evaluation
abeval phase structure --run-dir /workspace/evaluation
abeval phase security --run-dir /workspace/evaluation
abeval phase quality --run-dir /workspace/evaluation
abeval phase submission --run-dir /workspace/evaluation
abeval phase submission-review --run-dir /workspace/evaluation
abeval phase evaluate --run-dir /workspace/evaluation
abeval phase report --run-dir /workspace/evaluation
abeval publish --run-dir /workspace/evaluation --persist postgres,s3
```

Run only phases selected by `abeval plan`; for example, omit `submission-review`
when metadata disables it. All containers must mount the source and run directory
at their recorded absolute paths and supply the credentials needed by their
phase. Each phase container needs the application installed. Each phase records
evidence once; publication can be retried.

Use `onError: continue` where needed to preserve reporting and publication after
a failed check. The CLI retains dependency skips and failures in `run.json`;
its exit code describes the whole run, including earlier failed phases.

## Results and images

Read `run.json` for step and publication status, `reports/scorecard.json` for
policy/certification, and `reports/report.json` for completed AEH evaluation.
Do not infer success from a report file's existence. See
[persistence](persistence.md) for destinations and [images](images.md) for builds.
Trial images must be pullable by the workload cluster and use the same AEH
revision as the runner.

## Shared task integration

Expose `tiers` and `persist` as Pipeline parameters and forward them to the
shared task. Only the evaluation step calls the CLI:

```sh
abeval run --skill-id pack/my-skill --source /source \
  --tiers "$EVALUATION_TIERS" --persist "$EVALUATION_PERSIST" \
  --output /workspace/runs/taskrun-id
```

Defaults are Tier 1 and no persistence. The pipeline selects skills and tiers;
the CLI owns source staging, checks, reviews, trial execution, reports, and
PostgreSQL/S3/Compass publication.

Konflux retrieves trusted source and packages report evidence. OCI attachment
uses the existing digest-pinned `quay.io/konflux-ci/task-runner` image with
`select-oci-auth`, `retry`, and `oras`. Component `image-url` and `image-digest`
parameters select the attachment subject. Registry authentication is provided by
Konflux; the CLI has no registry settings or OCI publishing support.

For multiple skills, the shared task calls `abeval batch --skill-ids ...` once.
It then packages reports with `batch-package`, attaches the OCI archive with the
existing Konflux task-runner, and calls `batch-results` to emit small aggregates
and apply static/publication gates. The batch manifest identifies every skill
and source revision; registration consumes `summary.json` through the evidence
workspace. No Matrix or per-skill task fanout is involved. Parallel pipeline runs
remain allowed. The CLI's individual skill workflow and plugin staging are unchanged.

Only evaluation and trial images are maintained by this project. Use the same
AEH revision for both. Existing script-based bundles require their older images;
migrate bundle and runtime references together. The shared task repository has
Pipeline parameter examples and migration instructions.
