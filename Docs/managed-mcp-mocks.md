# Optional managed MCP mocks

MCPChecker can use an existing external MCP connection or an opt-in,
trusted mock. Harbor, ASE, A2A and AEH do not require `mcp` settings.

## Submission contract

External mode keeps `mcp.credentials_secret` and `mcp-config.yaml`. The Secret
can expose `MCP_URL` and optional `MCP_BEARER_TOKEN`. Existing environment
placeholders are expanded without printing credentials. External configuration
and Secret references remain trusted inputs, with their existing transport and
network access; managed-mode HTTP-only and NetworkPolicy restrictions do not
apply to external connections.

Managed mode instead selects one catalog item:

```yaml
name: cve-managed-positive
eval_engine: mcpchecker
description: Public CVE smoke
mcp:
  mock: cve-smoke
```

Do not set both targets. Managed mode generates an HTTP-only connection; no
submitted command, stdio server or fixture source is run by the model agent.
The agent and answer judge use the built-in MCPChecker agent. The pipeline
parameters `mcpchecker-agent-model` and `mcpchecker-judge-model` set their real
configuration; both default to `openai:claude-sonnet` via cluster LiteLLM.
The model has the mock's tool API, not Kubernetes or storage tools. The trusted
MCPChecker runner and its native model agent share a process/container; this
is not OS process isolation from the runner itself.

The working native layout is `apiVersion: mcpchecker/v1alpha2`, `config.agent`,
`config.llmJudge.ref` and `config.taskSets`. The old documented `spec.agent` /
`spec.judge` references are normalized, but task definitions must match the
installed MCPChecker version. See the runnable controls under
`examples/managed-mcp/submissions/`. The `mcpchecker-task-timeout` parameter
sets the native default per-task deadline, preserving task-specific deadlines;
a legacy `spec.taskTimeout` supplies that default when present. The Tekton
evaluation Task deadline bounds the complete suite.

## Trusted catalog and images

Only repository maintainers add `config/mcp-mocks/<id>.yaml`. A descriptor has
an image pinned by SHA256, nonprivileged listening port, MCP HTTP path,
optional nonsecret environment variables and a bounded lifetime. Unknown
fields, commands and unpinned tags are rejected. No submission-supplied
Kubernetes manifests are applied.

Build a new fixture image outside the submission pipeline, install its
dependencies in that image, push it and add its descriptor. Another mock needs
no pipeline code changes. The CVE fixture image contains only public fixture
data and emits structured `tool_call` events to stdout. An actual stateful
OSAC mock and source-build submissions are follow-up work.

The trusted runtime is built from the repository root with
`containers/mcpchecker-ci/Containerfile`; the fixture is built from
`containers/mcp-mocks/cve-smoke/`. Update the immutable runtime digest in the
Tasks and recovery CronJob after building. Runtime image selection is not an
untrusted PipelineRun parameter. Install the catalog into that trusted runtime.

## Cluster installation and execution

Use the dedicated `abevalflow-pipeline-mcpchecker` profile, following the
existing OpenShell profile pattern:

```text
prepare -> test -> start-mcp -> evaluate -> store
                                          finally: cleanup
```

The graph has no red-team, general evaluation branch, analysis or separate
result-handoff node. The single evaluation Task runs MCPChecker, validates its
run-specific output and publishes the four results in a second internal step.

Apply `config/mcp-mocks-rbac.yaml`, Tasks `start-mcp` and `evaluate-mcpchecker`
from `pipeline/tasks/components/`, the shared prepare/test/store Tasks, the dedicated
`pipeline/tasks/post/cleanup_mcp.yaml` finalizer, and `pipeline/pipelines/ci-pipeline-mcpchecker.yaml`. Install
`config/storage/mcp_recovery_cronjob.yaml` for orphan recovery. Adapt image
registry and namespace as part of the trusted deployment.

Use `pipeline/runs/mcpchecker-pipelinerun.yaml`: replace the submission directory
with an existing MCP submission in the selected repository, pin its revision
and the pipeline helper revision, then create it in the target namespace.
Before merge, use the reviewed feature commit for the helper revision. The
profile fixes the engine to MCPChecker; it has no engine-selection parameter.
The template includes the required identities:

- `start-mcp` and `cleanup`: `mcp-lifecycle`.
- `evaluate`: `mcpchecker-evaluator`, with
  `podTemplate.automountServiceAccountToken: false`.
- Preparation, testing and storage retain their existing identity.

The general and OpenShell pipeline graphs and their `cleanup-pvc` Task remain
unchanged; MCP archive/release behavior lives in the separate `cleanup-mcp` Task. The existing
webhook still selects the general pipeline; automatic profile selection is not
added. Select this profile explicitly through its PipelineRun template.

Manual PipelineRun authors are trusted operators. The reused preparation Task
retains its original shell-based parameter handling; this profile does not
claim to harden arbitrary untrusted direct PipelineRun inputs or the generic
webhook. New MCP lifecycle/evaluation arguments are passed directly to Python.

MCP runs require a fresh run-owned `volumeClaimTemplate`. Pre-existing/shared
PVCs are rejected before labeling or deletion. Other engines retain optional DB
configuration; a completed MCP acceptance run requires DB persistence.

A bare `tkn pipeline start` does not install these overrides. Use a PipelineRun
manifest with the overrides. Managed evaluation refuses a mounted SA token.
Provisioning and evaluation are separate Tasks, so retry a failed **whole
PipelineRun**, not only the evaluation Task. Every run has a new UID, PVC and
mock; a provision Task retry also uses its new Pod UID.

There is one fresh mock Pod and internal Service per run. Readiness performs
real MCP initialization and tool discovery, not just an HTTP health check.
The mock has no service-account token, filesystem mounts, credentials or
egress. NetworkPolicies permit its evaluator/readiness orchestration to reach
it; the evaluator can reach DNS, LiteLLM and that mock. DNS is a TCP/UDP
port 53/5353 exception to any destination, accommodating OpenShift host-network DNS;
it is not restricted to selected resolver IPs. Install in a namespace
without broader additive allow policies, using a CNI that enforces policy.
Namespace authors able to create arbitrary Pods/TaskRuns are trusted operators;
these policies do not make them untrusted tenants.

The evaluation Task publishes its run-UID-specific atomic result manifest,
validates that it belongs to this run, then exposes the four Tekton results.
Storage and Pipeline results refer directly to that one `evaluate` node.

## Evidence, failure and cleanup

Native JSON, process output/exit status, reports, MCP readiness, Pod image IDs,
mock audit and run provenance are archived under `mcp-runs/<run-uid>/` in
`ab-eval-reports`. A successful handoff requires the corresponding raw
artifacts before cleanup. Native provider, agent, timeout and judge errors are
infrastructure failures, not successful deliberate-negative evaluations.
Valid failed assertions still produce a report and score.

The single finalizer runs a temporary archive worker mounting the source PVC,
then removes that worker before releasing the PVC holders. It writes the MCP
report to PostgreSQL idempotently by **run UID**, uploads through the Python
MinIO client, downloads every upload and checks its SHA256, then commits
`archive-complete.json`. Only then can mock resources and workspace be deleted.
Legacy custom pipelines retain a Pod-log-only path with their existing RBAC.
No `mc` binary or `oc exec` inside MinIO is needed.

The workspace and mock have no PipelineRun owner reference. An archive-protection
finalizer prevents normal PipelineRun pruning before archival. Do not force
remove that protection or use foreground cascading deletion as a cleanup method.
For cancellation, the cluster must retain cancelled TaskRun Pods (Tekton
`feature-flags.keep-pod-on-cancel=true`) or supply final snapshots before Pod
deletion. This is a cluster administrator setting with cluster-wide impact.
Timeout retention is version dependent and must be verified separately.
The finalizer checks the run-owned TaskRun inventory, including retry Pod names;
missing final logs or stopped-Pod identity block cleanup. Enabling retention
after logs were lost cannot recover that old run. Do not remove archive
protection to hide missing evidence.

On archive failure, evidence and resources remain, failure is visible, and the
recovery sweep retries each retained workspace independently. Failed archive
workers are replaceable; they never own the evidence. Mock Pods have a deadline
so retained failed runs do not run indefinitely. Investigate permanent missing
evidence rather than bypassing the archive gate.

Download logs with `scripts/fetch_run_logs.sh RUN NAMESPACE OUTPUT`. The helper
supports the new UID layout and historical layouts. For a local client,
port-forward MinIO and set `MINIO_ENDPOINT=http://127.0.0.1:9000`; it requires
read access to `minio-credentials` and never prints its contents. Compared with
the original helper, the local client now needs Python with the `minio` package
(install the repository dependencies) and access or a port-forward to MinIO;
it no longer executes inside the MinIO server. Historical archive layouts
remain readable.

## Acceptance contract

Run both provided controls through the complete Pipeline, with separate PVCs:

- Positive: score **1.0/pass**, successful `get_cve_by_id(CVE-2016-10739)`,
  answer judge passes.
- Deliberate negative: score **0.0/fail** only because `minToolCalls: 10000`
  fails. The real tool call and answer judge must still pass.

The combined smoke is **1.0/pass** only if both controls match, PostgreSQL has
exactly one run/task per UID, all required MinIO artifacts download with matching
hashes, and mock resources and workspace PVCs are confirmed absent. A transport
error, missing output or provider failure fails the smoke.

Unit/contract coverage belongs to this CI implementation. Deployed acceptance
covers the dedicated profile graph, direct Task results, CNI isolation, lifecycle/archival boundaries,
storage readback, cancellation and recovery. Two concurrent mocks need two free
Services in quota. Record any unavailable gate as unverified, not passed.

For a source-only controller update, `Containerfile.refresh` can reuse an
already trusted runtime digest with its installed dependencies:

```sh
podman build -f containers/mcpchecker-ci/Containerfile.refresh \
  --build-arg CI_BASE_IMAGE=REGISTRY/mcpchecker-ci@sha256:DIGEST .
```

This is a maintainer build, not a submitted source-build feature.
