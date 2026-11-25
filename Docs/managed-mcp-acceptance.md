# Managed MCP mocks: implementation evidence

Recorded 2026-11-16. This is an evidence record, not a claim that every deployment gate passed.

## Source and review

Feature branch: `codex/mcpchecker-managed-mocks`, based on upstream main
`d818cedeac29b627b761b548ef4ee4beebc92334` after prerequisite PR #127 merged.
Two independent `gpt-6.1-terra` high-reasoning reviewers and one
`gpt-6.1-sol` medium-reasoning reviewer reviewed architecture, lifecycle and
acceptance behavior. Findings led to fixes for provisioning/finalization races,
UID ownership, retained evidence, per-task versus suite timeouts, cancelled/retry
Pod inventory and interruption between archival and workspace release.
Review agreement is not deployed verification.

The earlier guarded-runtime repository test run passed **1,198 tests**, with 10 skipped
and six existing SciPy precision warnings. It used the repository CI selection
(`tests/`, excluding the separate observability job's `test_mlflow_observer.py`).
Ruff checks/formatting passed for the CI scope and new fixture, 151 Python files.
Affected Kubernetes/Tekton resources passed server-side dry-run validation.

## Dedicated profile validation

The current design uses `abevalflow-pipeline-mcpchecker`:
`prepare -> test -> start-mcp -> evaluate -> store`, with one `cleanup-mcp`
finally Task. Evaluation and result publishing are two steps in the same Task.
The generic pipeline, its evaluator/cleanup, webhook manifests, Oracle run and
maintenance files match upstream; the shared store only adds an optional UID
input. The log-fetch helper has documented local Python/MinIO prerequisites.

Current runtime: `sha256:d74842d2ee9fa8955349ccda7a47d589856c1f1cf81ae0dcc59921524e85bfda`.
The native runner source and catalog use evaluator label `evaluate` and a
14,400-second fixture lifetime. The manual PipelineRun owns timeout settings;
they are not unsupported Pipeline fields.

The repository CI selection passed **1,199 tests**, with 10 skipped and six
existing SciPy warnings. Lint/format checks passed for 151 Python files.
Dedicated Pipeline/Tasks and the manual run passed cluster schema validation.
Three independent reviewers rechecked the profile/source boundary.

Fresh live CNI tests against label `evaluate` passed DNS/LiteLLM/own MCP access,
MinIO/Kubernetes/unrelated-client denial and mock egress denial. Evidence was
verified on upload under `mcp-runs/managed-profile-cni-20261116/`, then those
resources were removed. This still does not establish two-Service concurrency.

The final-runtime controls both completed all six Tasks, with no skipped nodes:

- Positive `mcp-profile-positive-p8xtf`, UID
  `4f5dd8e1-96a2-4f7c-900d-93ec06cf793d`: **1.0/pass**.
- Deliberate negative `mcp-profile-negative-rxsw2`, UID
  `6a9f9c97-ad08-4858-b784-5982648b2766`: **0.0/fail**. The actual CVE tool
  call and answer judge succeeded; the artificial minimum of 10,000 tool calls
  failed as intended.

Verifier Pod `mcp-profile-verifier-nvbtn` confirmed combined smoke **1.0/pass**:
31 downloaded artifacts per control matched their recorded bytes and SHA256;
PostgreSQL contained exactly one run/task per UID; native models, process status,
mock readiness/audit/image identity and final source SHA matched. It also checked
all five DAG Tasks in the final evidence inventory, exact six-node run graph,
cleanup results, resource/PVC absence and removal of run protection. A repeated
`finish` call after workspace release successfully verified and reused each
committed archive.

External-Secret run `mcp-profile-external-hsm28`, UID
`41da8bf2-0ada-4726-ab4b-8e78f612884e`, completed all six Tasks without skips
at **1.0/pass**. Verifier `mcp-profile-external-verifier-rjzz9` checked 26
archived objects, PostgreSQL uniqueness, successful native CVE tool/judge
behavior, resource/PVC absence and that no managed target was created. Its
separately operated public fixture was removed after verification.

Three fresh independent reviewers then checked code, YAML/deployed configuration,
and PipelineRun/database/artifact evidence separately. The code reviewer executed
57 focused tests; the YAML reviewer executed 42 tests and compared deployed specs
with source. Neither found a blocking defect. The persistence reviewer directly
queried PostgreSQL and confirmed exactly one run/task per UID, scores 1/0/1,
zero errors/skips and successful tool/judge checks. Their separately authored
GET-only MinIO worker `mcp-independent-minio-w4jhl` downloaded and hash-checked
all 31/31/26 objects and checked native results, inventory and cleanup markers.
These reviews supplement, rather than replace, the remaining deployment gates.

One nonblocking least-privilege observation remains: mock ingress permits all
Task Pods carrying the same PipelineRun label, including trusted orchestration,
while evaluator egress is restricted to `evaluate`. Narrowing ingress to
`evaluate` and `start-mcp` would match the original two-client policy more
closely. No cross-run bypass was demonstrated; the current implementation should
not be described as limiting ingress to exactly those two Tasks.

## Earlier shared-graph evidence (superseded profile design)

The results below predate the dedicated MCPChecker profile. They establish
controller/native/storage behavior for those revisions, not validation of the
new graph. The final profile requires fresh deployed acceptance.

## Deployed evaluation controls

The native MCPChecker agent and answer judge both used `openai:claude-sonnet`
through the existing LiteLLM route. The mock provided a real streamable-HTTP MCP
connection with initialization, tool discovery and `get_cve_by_id` against a
public CVE fixture. No additional filesystem, shell or stdio tool was offered
to the managed model agent.

Timeout-corrected runtime:
`sha256:cf63a2b0b5b5d79d4bfb36a0fb63fa014a8d4b30c6d872b3c176de8f8feed2f3`.

- Positive `managed-cve-positive-7jrft`, UID
  `22f1e80b-6131-49e3-b3bb-ba2e01b4cabb`: **1.0/pass**.
- Deliberate negative `managed-cve-negative-4klk7`, UID
  `f69bfef1-e751-4dbb-b8e9-608a69450847`: **0.0/fail**, because the artificial
  minimum of 10,000 tool calls was not reached. Its actual CVE tool call and
  answer judge passed.
- Combined control agreement: **1.0/pass**. The verifier downloaded and checked
  33 archived artifacts per run, matching size and SHA256; verified exactly one
  PostgreSQL run/task per UID; checked native output/process/models/readiness,
  mock digest and audit; and confirmed managed resources and PVCs absent.

Earlier DNS failures were infrastructure failures, not negative-control passes.
The first DNS policy did not reach OpenShift's host-network DNS backend on
port 5353. The corrected policy allows TCP/UDP 53 and 5353 to any destination;
this is an explicitly broader DNS port exception, not resolver-IP confinement.

These test PipelineRuns used the production pipeline graph with only its
preparation Task replaced by a trusted public-fixture seeder. The seeder also
cloned upstream main for existing downstream scripts. Webhook/prepare configuration and validation logic were checked locally;
actual submission checkout/prepare integration was not exercised through that
fixture preparation. Native runs use the installed checker's task format;
this is not proof that every historical task document is compatible.

## Isolation and other routes

Live CNI probes verified the managed evaluator could reach DNS, LiteLLM and
its own mock while MinIO, Kubernetes API and an unrelated mock were denied.
The mock could not initiate egress. Those probe resources were archived and
removed. This was two evaluator clients, not two concurrent managed Services.
The evidence prefix is `mcp-runs/managed-cni-proof-20261116/`.

The non-MCP route `managed-generic-route-28zdb` completed with the MCP branch
skipped, real result bridge and finalizer. Its producer was a small trusted
result fixture (0.75 treatment, 0.25 control), not a full ASE/Harbor benchmark.

External-Secret run `external-cve-positive-nkbxm`, UID
`1efbaf32-ee56-41e7-b49a-750eb0e2ee2f`, completed **1.0/pass** on the
timeout-corrected runtime. Read-back verified 28 artifacts, PostgreSQL run/task
uniqueness, successful native CVE tool/judge behavior and PVC absence. No managed
mock target was created. Its separately operated public fixture was removed
after verification.
External configurations and Secret references remain trusted inputs with their
existing transport and network permissions; managed isolation does not apply.

## Cancellation finding and recovery boundary

A dedicated cancellation/archive-failure fixture `managed-lifecycle-hxtxh`
(UID `d3ee857e-4514-4011-8d52-f14f05bc0990`) visibly failed its finalizer when
its own archive worker used an invalid MinIO endpoint. The shared Secret and
MinIO server were not changed. A retained-state probe verified one mock Pod,
one Service, two NetworkPolicies, one PVC and run protection remained, without
an archive-complete marker. A manually triggered recovery CronJob then archived
its available evidence and released those resources.

**That experiment did not preserve the cancelled hold Pod's log.** A stricter
read-back found Tekton had already deleted it. The cluster has
`keep-pod-on-cancel=false`; retaining cancelled Pods is a cluster-wide,
Operator-managed prerequisite. No global setting was changed. The earlier
recovery result therefore does not satisfy complete cancellation evidence.

The final source corrects this by verifying the union of owned TaskRuns and
PipelineRun child references, including every retry Pod identity. Unknown Pod
identity, missing final logs, nonterminal snapshots or UID mismatch block
archive certification and deletion. Run protection remains until PVC release
is confirmed. An interrupted release can reuse only a committed archive whose
identity, entries, lengths, hashes, inventory and cleanup marker verify.
Those guards do not reconstruct already lost logs. Unit tests cover missing
cancelled/retry Pods, delayed child references, unknown identity, invalid
snapshots, archive corruption and interrupted release routing.

## Final guarded runtime

The current profile validation above supersedes the earlier shared graph.
Complete cancellation preservation is still unverified; the source guards
block missing final evidence rather than reconstructing lost logs.

## Accepted cancellation limitation

On 2026-11-16 the user accepted the current delete-on-cancel behavior and possible
loss of cancelled Task Pod logs for now. Keep `keep-pod-on-cancel=false`; complete
cancellation-log preservation is deferred rather than a release gate. No cluster
setting or source guard was changed. If cancellation loses required evidence,
the existing archive guard still reports failure and retains the remaining mock
resources/workspace; accepting log loss does not certify an incomplete archive
or introduce automatic deletion of those protected resources.

## Remaining deployment gates

- Full timeout log preservation remains unverified; it needs a live test with
  an exact known log marker and, if necessary, a retention mechanism.
- Two concurrent managed Services could not run: namespace quota is 10 and
  nine Services belong to existing shared applications. The current identity
  cannot raise ResourceQuota. Shared Services were not deleted. Unit identity
  tests and live cross-client isolation do not replace concurrent-run evidence.
- A second descriptor was exercised by configuration tests, not a second live
  mock image. Adding a descriptor requires no pipeline code change.
- OSAC case migration, building submitted mock source, forced foreground
  cascading deletion, node-loss recovery and arbitrary evaluation-Task-only
  retries are outside the demonstrated acceptance boundary.

When archival completes, durable evidence is stored under `ab-eval-reports/mcp-runs/<UID>/`.
`scripts/verify_managed_mcp_smoke.py` implements the positive/negative control
read-back. See [deployment and operational contracts](managed-mcp-mocks.md).
