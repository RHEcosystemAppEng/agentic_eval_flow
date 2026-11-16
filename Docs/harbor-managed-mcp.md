# Native Harbor skills with managed MCP mocks

This is an opt-in skill-evaluation profile. Generic Harbor and the existing
MCPChecker profile remain available without managed mocks. The initial managed
Harbor profile requires `mcp.mock`; it does not yet support an external MCP
credentials Secret. AEH and the production OSAC MCP server are unchanged.

## Scope and comparison

One PipelineRun evaluates one native Harbor task once per condition, sequentially,
using Claude Code and the cluster `claude-sonnet` route. Control and treatment
receive the same instruction, reference docs, agent image and tool catalog. Only
treatment receives `environment/skills`. Each condition gets a fresh mock Pod
and Service. This first pair is an integration smoke, not statistical evidence of
skill benefit. Subsequent per-skill suites require a separately verified reset
between tasks; the runner currently rejects multiple tasks/attempts.

The OSAC example lives in `RHEcosystemAppEng/skill-submissions`, branch
`codex/osac-create-uncertain-harbor`, submission `osac-vm-create`. It adapts
CREATE-UNCERTAIN into a native task with instruction.md, task.toml, environment/
and tests/. The skill is our unchanged committed tenant-vm-create version from
osac-ai-skills commit 3c1c53a, not the lost temporary candidate used by the old
matrix. The submission records the source hashes. Both conditions get the six
original repository reference files; no reference guide/index was generated.

## Install and run

1. Build containers/mcp-mocks/osac-tenant-vm/Containerfile and pin its image in
   config/mcp-mocks/osac-create-uncertain.yaml. New mocks need a prebuilt image
   and trusted descriptor, not changes to pipeline code. The isolated fixture
   has no connection to OSAC and logs sanitized structured tool-call outcomes.
2. Build containers/harbor-agent.Containerfile and harbor-mcp.Containerfile
   against the pinned eval-base. The agent image copies no controller/mock code.
   Pin both resulting digests in the new Tasks/run template. Run `python -m scripts.render_mcp_catalog` to render an immutable,
   content-addressed catalog ConfigMap from all
   config/mcp-mocks/*.yaml and pass its name as mcp-catalog-configmap. Apply
   config/harbor-mcp-catalog.yaml before the run. Only the trusted start/evaluate
   Tasks mount this catalog read-only; native child Pods never receive it.
   Both conditions bind the same immutable catalog, preventing mid-pair drift.
   A new mock needs an image, descriptor/catalog refresh and run parameter,
   not changes to pipeline code or a controller rebuild.
3. Install config/mcp-mocks-rbac.yaml (existing tokenless child SA and archive
   worker), then config/harbor-mcp-rbac.yaml. The Harbor controller alone adds
   pods/exec permission. Install the Harbor Tasks, Pipeline, and the updated
   config/storage/mcp_recovery_cronjob.yaml before creating a run. The shared
   recovery runtime must include the Harbor validators, because it sweeps both
   engines' protected workspaces.
4. Use pipeline/runs/harbor-mcp-pipelinerun.yaml. Pin submission and pipeline
   revisions to reviewed feature commits. Set harbor-case, harbor-model and
   harbor-agent-image. Default model is Sonnet. Existing llm-credentials,
   minio-credentials and ab-eval-db-credentials are required; no key is a YAML
   parameter. Keep service quota headroom for one mock; conditions run sequentially.

The graph is prepare -> test -> start-mcp -> evaluate-harbor -> store, with one
archive-gated finalizer. evaluate-harbor is trusted orchestration. Only its
native child uses pipelineTask=evaluate, tokenless mcpchecker-evaluator SA and
the restricted own-mock/DNS/LiteLLM egress policy. Do not rename the trusted node
to evaluate. Agent and answer judge get explicitly scoped model credentials;
cluster lifecycle and storage credentials are not passed to the child.

## Scoring and evidence

The native verifier correlates tool-use IDs with actual results. It checks a
single create, successful caller/catalog observations, the expected
DeadlineExceeded, and a successful subsequent read of the returned VM in STARTING.
The answer judge sees those observations rather than assumed scenario success.
A failed read plus a fabricated answer fails. Reward is 1 only if behavior and
answer checks pass; a valid agent failure is 0. Missing native output, provider
exceptions, missing judge output or inconsistent server audit are infrastructure
failures, not scored skill failures. The runner still evaluates both conditions
when a completed native trial legitimately earns 0.

Each attempt archives native output/process logs, trajectory, behavior and judge,
readiness/target, exact mock and child Pod identities/images/terminal snapshots,
and mock audit before deleting resources. Archive upload verifies read-back
checksums. After deletion it archives the exact cleanup confirmation. Final
validation checks both archives, distinct producer UIDs, pinned runtime image
identity, readiness endpoint, native/audit outcomes, reward agreement and cleanup.
The finalizer stores the report under the same immutable run UID as the normal
store Task; PostgreSQL retry is idempotent. MinIO paths use mcp-runs/<run-uid>/.

Archive failures retain raw workspace/resources for the existing recovery sweep.
Delete-on-cancel remains the previously accepted evidence limitation. True
concurrent-run acceptance is deferred because the current Service quota has one
free slot. Passing source tests is not deployed acceptance; record the actual
PipelineRun UID, rewards, DB read-back, downloaded checksums and resource absence
in the acceptance evidence before claiming the integration works.
