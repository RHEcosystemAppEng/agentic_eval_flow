# Native Harbor managed-MCP acceptance evidence

## Latest paired rerun with corrected catalog verification

PipelineRun `harbor-osac-create-jj2rz`, UID
`d789441a-a4fd-48ce-8daa-409110dabf9b`, uses submission
`43e6af3ecbe682c8136ee48616f2a2fa1f7fedbe` and the same CI revision, model,
images, documents and unchanged skill as the preceding pair. Both arms reran
because the evaluator changed. All 38 local verifier/shell integration tests,
Ruff checks, formatting and diff checks passed. No PR was opened.

The corrected catalog gate accepts successful pre-create List or Get evidence
containing the same selected published record. Ten positive/negative fixtures
cover list acceptance and wrong IDs/names/states, split records, failed reads,
wrong resource type, post-create reads, malformed and empty results. The answer
rubric now explicitly assesses unsupported root causes, timing and named default
scope as well as core VM facts. The reward conjunction remains unchanged.

Stored native results are **control 0.0 / treatment 1.0**:

- Both task-completion checks pass: exactly one create, requested inputs/defaults,
  expected timeout, and successful current-status reconciliation.
- Control fails protocol only because it omitted `check_connection`. It makes
  six MCP calls, including a rejected filter and successful recovery.
- Treatment passes protocol, spontaneously loads `tenant-vm-create`, and makes
  five MCP calls: connection, catalog list, create, VM list and VM get.
- Both stored answer judges pass. Neither agent reads the separate reference
  library. Treatment's registered skill content and successful Skill call are
  visible in its native trace; availability is not being substituted for reading.
- Both native processes exit 0 with zero native exceptions.

**Independent review limits the interpretation of the answer passes.** Treatment
asserts `tenant-blue` is the caller's selected default; a visible-tenant summary
does not establish the selected default or VM membership. The judge explicitly
excuses this unsupported assignment as minor despite the stricter rubric.
Treatment also recommends polling the VM state until `READY`; the real
ComputeInstanceState enum has `RUNNING`, while READY belongs to a separate
condition that the current MCP summary does not expose.
Independent semantic review therefore fails both answers under the written
all-material-claims rubric, separately from their stored judge verdicts.
Control also asserts a causal order for the timeout and an eventual running
state without sufficient returned evidence. These are reporting and judge
reliability gaps, not transport failures. Preserve the original scores and judge
reasons; do not silently rescore them or describe treatment's 1.0 as an
unqualified independently validated answer. This one pair supports observed
protocol improvement and successful skill activation, not a general success-rate
uplift or a fully reliable semantic-accuracy evaluator. No further model runs
were launched to seek a more favorable result.

All six pipeline Tasks succeeded with zero skips. Independent downloads
hash-checked 85 root, 28 control and 29 treatment manifest entries (overlapping
inventories). PostgreSQL has exactly one UID-keyed Harbor run and two trials
with the stored 0.0/1.0 scores; authorized storage replay preserved the same row
UUIDs and values without duplicates. The original six Task Pods and Bound PVC
`pvc-9762350dd9` remain with run protection; console logs are readable. Managed
mock/native-child Pods, Services and NetworkPolicies are absent. The shared
recovery sweep remains suspended and inactive. Live child snapshots also
verified tokenless agent/mock isolation and the pinned image identities. These
checks prove faithful persistence of the scores, not semantic judge correctness.

Evidence is under
`ab-eval-reports/mcp-runs/d789441a-a4fd-48ce-8daa-409110dabf9b/`.
The historical results remain unchanged and retained for comparison. Deletion
was permitted but has not been performed.

## Corrected task and separated scoring (November 16)

Ordinary pair `harbor-osac-create-nljcx`, UID
`abde07e0-fc92-45f7-ba7f-b463a697eb20`, used submission
`64ce3a4db18d087f6224f2cdd2f55115a9b7090d` and CI revision
`fb9c8a4714e14c2e13ab3a9d383d150bb0d2210c`. The skill bytes and pinned
runtime/mock images were unchanged. Shared instructions now authorize necessary
read-only checks in this unattended task, which explicitly requests verified
current status. The verifier separates task completion, protocol compliance and
answer accuracy; composite reward remains their conjunction. Local verifier and
shell integration checks passed all 28 cases before this run.

All six Tasks succeeded, no Tasks were skipped, and both native processes exited
0 with zero native exceptions. Both agents created once, recovered after the
intended timeout, read the VM and reported STARTING. Both passed task completion
and the answer judge, but received composite 0.0 for distinct protocol checks:

- Control omitted `check_connection`; it made six MCP calls, including recovery
  from a rejected filter, and did not read the reference documents.
- Treatment spontaneously invoked `Skill("tenant-vm-create")`, successfully
  loaded its content, then made five MCP calls. It checked the connection,
  selected the published item from the catalog list, created once, listed VMs
  and read the exact VM. It did not perform a separate catalog get, which that
  verifier revision required. Review substantiated that List and Get provide
  the same projection and the unchanged skill allows inspection from the list;
  the separate-get requirement was an evaluator error.

This shows task recovery and ordinary skill activation working. The combined
0.0 scores must not be described as failure to create or verify the VM. One pair
cannot establish a general skill benefit. The answer judge passed both answers;
that verdict is not proof of every causal explanation or default-tenant inference
in the prose.

Independent readback hash-checked 85 root, 29 treatment and 28 control manifest
entries (overlapping inventories; 146 actual exported objects). PostgreSQL had
exactly one UID-keyed Harbor run and two trials. Replaying storage preserved
the same row UUIDs and values without duplicates.

All six Task Pods and PVC `pvc-9051528aa0` remained for console inspection;
cleanup reported retained, run protection remained, and recovery was suspended.
Run-scoped mock/native children, Services and NetworkPolicies were absent.
MinIO evidence is under `ab-eval-reports/mcp-runs/abde07e0-fc92-45f7-ba7f-b463a697eb20/`.

### Explicit skill diagnostic (excluded from benefit comparison)

A separate treatment-only diagnostic at submission revision `64ce3a4` explicitly
asked to use the skill. Its original score was not recomputed under later
verifier changes. UID
`d8ade211-b501-46bd-8d79-4fcd0de91e2c` scored 1.0, with successful correlated
Skill loading and all three dimensions passing. Its registered skill SHA256 was
`ee2dcb2c6eb27a2cc7569e2c1cf8a82b39540e2ac95b65a378c5f804939d0f14`.
Harbor stages skills from `/workspace/skills` into
`$CLAUDE_CONFIG_DIR/skills`; Claude's config directory is `/logs/agent/sessions`.
The ordinary pair also archived that exact registered path
`/logs/agent/sessions/skills/tenant-vm-create/SKILL.md`, matching the source hash,
and recorded `skillUsage` count 1 plus successful Skill content delivery.
Control has no tenant skill. There is no database evaluation row for this diagnostic. Independently downloaded
and hash-checked 98 diagnostic and 29 attempt manifest entries (overlapping
inventories), including the preserved skill and trace evidence.

Manual upload introduced macOS AppleDouble metadata files, causing the original
post-processing driver to exit 1 after the successful native trial. Archival was
recovered without a model rerun by quarantining only magic-verified metadata,
recording hashes and preserving the original error. The native CLI exit status
was not separately persisted before that crash and is recorded as unknown;
saved native output reports no exception. The diagnostic is not a clean ordinary
CI pair and is not counted in skill-benefit claims.

### Comparison with the previous local MCPChecker experiment

Surviving reviewed decisions record CREATE-UNCERTAIN control 9/9 and treatment
8/9: Sol 6.1 low 3/3 versus 3/3, Luna 6 high 3/3 versus 2/3, and Terra 6.1 medium
3/3 versus 3/3. Luna's failed skilled repetition found the VM but omitted timeout
disclosure. Visible successful skill reads were respectively 3/3, 1/3 and 3/3.
Sol's MCP calls were 10,10,8 without skills versus 6,6,6 with skills; Terra's were
10,10,10 versus 6,6,6; Luna's were 15,11,7 versus 10,7,7. The old case therefore
shows no success-rate uplift, with an indication of fewer calls in skilled runs.

Those local runs used a custom Codex CLI adapter inside MCPChecker, different
models, and a different skill hash. All six reference-document hashes match,
but the exact frozen shared prompt and original raw traces no longer exist.
The local numbers come from surviving scorer and six-reviewer adjudications,
not a fresh raw-trace audit. Mock server and scoring revisions also differ.
These results cannot identify a Harbor, Sonnet or skill regression. The latest
Harbor pair closes the previously observed stop-and-ask recovery gap; remaining
combined failures concern protocol evaluation, not timeout recovery.

## Earlier corrected run with retained logs

PipelineRun `harbor-osac-create-8ts4m`, UID
`8323f39d-27d6-4ce4-bfe1-01b988cdb430`, completed all six Tasks successfully
with zero skipped Tasks. It used CI revision
`9468049e76ccdbbfc4158e96eee42b01dc9623dc` and controller image digest
`sha256:734aab23853c5e42ca152614f3562b6e22fabc258caa8af8e537d9e6700399a3`.
All other immutable inputs below remained unchanged. Treatment ran first,
followed by control, each with a fresh mock and native child.

This run verifies the corrected redaction boundary, complete paired lifecycle,
live console output, persistence and intentional debug retention together:

- Both native processes exited 0; the strict attempt and final archive validators
  passed without relaxing their identity or scoring requirements.
- Harbor CLI output and flushed start/exit/archive milestones appeared during
  execution. Every archived process-log line was found in terminal console logs.
  Full agent tool events remain in the native trace artifacts.
- All six Task Pods and exact PVC `pvc-6812ff51d3` remained after completion.
  The PVC was Bound with no deletion timestamp. Cleanup reported `retained`,
  and workspace release was explicitly skipped. The run protection finalizer
  remained, and recovery was suspended with no active sweeper. The PVC's
  matching PipelineRun owner reference remained/reappeared under Tekton; this
  evidence does not claim that the retained PVC is detached from its parent.
- Managed mock/native child Pods, Services and NetworkPolicies were absent
  after verified per-attempt archival.
- Independently downloaded and hash/size-checked 83 root, 28 treatment and
  27 control archive objects. These inventories overlap, not distinct-file totals.
- PostgreSQL contained exactly one UID-keyed run and two trials. Authorized
  storage replay preserved the same row UUIDs and values with no duplicates.
- MinIO evidence is under
  `ab-eval-reports/mcp-runs/8323f39d-27d6-4ce4-bfe1-01b988cdb430/`.

The evaluation result is still **control 0.0 / treatment 0.0**, recommendation
`fail`. Both native traces contain exactly catalog list, catalog get and one
create call returning the intended timeout; neither performed a post-create
VM read or `check_connection`. Neither read docs or invoked Read/Skill. The
treatment advertised `tenant-vm-create`, while control did not. Thus the
integration and skill exposure work, but skill uptake and benefit were not
demonstrated. A successful pipeline here means trustworthy execution and
persistence of a valid failed evaluation, not a passing tenant task.

Three independent reviewers checked the final live configuration/retention,
actual trace/evidence bytes, and archive/database persistence. The earlier
failed runs below remain failed; none was retroactively rescored. No PR has
been opened. Debug resources remain intentionally retained.

## Completed integration smoke

PipelineRun `harbor-osac-create-wshwp` completed all six Tasks successfully with
zero skipped Tasks. Run UID: `f84a4e36-4a83-4ac3-a987-5ea98a9f34c6`.
This demonstrates one complete native Harbor control/treatment lifecycle against
an isolated mock. It does not demonstrate task success or skill benefit.

Both control and treatment earned **0.0**, with recommendation `fail`. Each
agent listed and inspected the catalog, called create once, received the intended
DeadlineExceeded, then asked permission rather than reading the existing VM.
Neither performed `check_connection` or the required post-create read. Both
behavior and answer checks failed with no native infrastructure exception.
The treatment's native initialization advertised `tenant-vm-create`; control
did not advertise it. Neither condition called Read or Skill or read reference
docs. Exposure worked; activation and benefit were not demonstrated by this pair.

## Immutable inputs

- CI revision: `ec94a3db89f175f3a4772ef7a2ec4e8716452a59`.
- Submission revision: `0e009ff4399514b13a171e3588280fc85ebdf5fc`.
- Submission: `submissions/osac-vm-create/harbor-tasks/create-uncertain`.
- Agent: native Claude Code, cluster `claude-sonnet` route.
- Agent image digest: `sha256:df7314514385ad2875f8dfa05c24563647a2b6330803bac7ea206e33ecececc2`.
- Mock image digest: `sha256:0dbd7081b1bf7639d7fa1551a8fff74291b4caac8a93e64e73d9df8de9f1c181`.
- Controller digest: `sha256:42d7b35ae0f9066008df8fea659302030ffc91608499afe1b6786b89ce9aa1e2`.
- Immutable catalog: `harbor-mcp-catalog-92531c992f22aa35`.
- Unmodified committed skill source: osac-ai-skills commit
  `3c1c53a7e0bbcc2d42d58e7dfbbc7136c35d9a53`, v0.1.0, SHA256
  `ee2dcb2c6eb27a2cc7569e2c1cf8a82b39540e2ac95b65a378c5f804939d0f14`.

This recovered committed skill differs from the lost temporary candidate used
by the earlier evaluation matrix. Scores are not comparable with that matrix.
Both conditions received the same six original repository documents, task,
model and agent image. Treatment alone received the tenant skill.

## Executed boundary verification

Three independent reviewers checked source/native traces, installed YAML and
isolation, and persistence. Their conclusions were corroborated with executed
checks rather than treated as proof by agreement.

- Native child and mock ran tokenless with the pinned runtime image identities.
  Native children had only workspace/tmp volumes, no controller credentials,
  catalog mount or mock source. Trusted orchestration remained outside the
  restricted evaluator policy selector.
- Each condition used distinct fresh mock and native child Pod UIDs.
- Independently downloaded and SHA256/size-checked 83 root archive objects,
  27 control attempt objects and 28 treatment attempt objects. These inventories
  overlap; the counts are not distinct-file totals.
- Verified readiness/target identities, native/audit tool calls and outcomes,
  behavior/judge/reward agreement, terminal producer snapshots, and deletion
  receipts for both attempts.
- PostgreSQL contained exactly one UID-keyed Harbor evaluation run and two
  trials, with rewards 0.0/0.0. Replaying storage preserved the same row UUIDs
  and values; no run-name duplicate was created.
- Root cleanup receipt verified. Original workspace PVC `pvc-5db80106f5` and
  every run-scoped Pod, Service and NetworkPolicy were absent after finalization.
  The run protection finalizer had been removed.
- Evidence is stored in MinIO bucket `ab-eval-reports` under
  `mcp-runs/f84a4e36-4a83-4ac3-a987-5ea98a9f34c6/`, including per-attempt
  manifests at `attempts/control` and `attempts/treatment`.

## Earlier attempts are not completed pairs

- `harbor-osac-create-mfbxx`: failed before agent execution because Python 3.11
  required typing_extensions.TypedDict in the fixture. Archived and cleaned.
- `harbor-osac-create-r7fcf`: treatment ran, but judge JSON parsing stopped
  completion. Parser corrected to accept exactly one unambiguous verdict.
  Archived and cleaned; no complete pair.
- `harbor-osac-create-m485d`: cancelled before mock/agent execution. The accepted
  cancellation limitation left evidence PVC `pvc-85f5ea0e61` retained because
  a deleted test Pod log could not be collected. No score is attributed to it.
- `harbor-osac-create-9q26q`: control ran, but Kubernetes client log deserialization
  wrapped raw bytes as a string. Strict audit validation rejected it. Corrected
  by reading raw responses and decoding once, confirmed with an actual API probe.
  A diagnostic in-memory replay did not alter the original failed archive or
  retroactively count it as a successful pair.

## Debug visibility follow-up

The completed run used normal cleanup, which deleted its Task Pods and made
OpenShift console logs unavailable. Its full logs remain archived. At the user's
request, the Harbor profile now defaults to retaining Task Pods and the workspace
after verified archival. The shared recovery CronJob is temporarily suspended
with no active sweeper. Native child/mock resources still archive and delete
between conditions. Harbor CLI output now also streams to the Task console;
full native conversations remain separate artifacts.

Run `harbor-osac-create-wdhrf`, UID `d2f9c91d-d032-468a-8027-5a403d0d4f7e`,
used CI `c85ab19` and controller digest
`sha256:707c428694651d2b278d576fc60deb7aeae755827866a36f6fb4395489e2a266`.
It demonstrated live Harbor CLI output/milestones and retained terminal logs,
but failed after treatment in strict attempt identity validation. Control and
store did not run; PostgreSQL correctly has no evaluation row for this attempt.
Its finalizer archived 54 root objects and reported `cleanup-status=retained`.
All five created Task Pods and exact PVC `pvc-fc0fa061bc` remained, with run
protection present and recovery suspended. This is debug-retention evidence on
a failed infrastructure path, not a completed pair.

The failure came from newly added broad storage-username redaction corrupting
Kubernetes label keys in attempt snapshots. The root finalizer recollected the
same Pod UIDs with intact keys, confirming the cause. The fix limits additional
username masking to process/console text, preserves prior token/password/DB URL
masking for proof documents, and leaves the identity validator unchanged. A
regression test and an in-memory replay of the actual snapshots passed; neither
changed the failed archive. The corrected complete run is recorded above.
No PR has been opened.

## Scope and remaining limits

This covers one Sonnet case with integration pairs against a mock, not real
OSAC or a multi-case skill benchmark. Concurrency remains deferred under the namespace Service quota.
Cancellation has the previously accepted evidence limitation. Durable per-run
retention exemption is not implemented: resuming the shared recovery sweep can
release retained completed runs, so inspection and deliberate release must
precede resumption. Production OSAC MCP and skill source were not changed.
Generic Harbor and AEH agent paths remain unchanged. MCPChecker shares lifecycle/recovery helpers, and
its orphan recovery is temporarily paused as described above.

## Local checks

The final console/retention source passed the repository CI test selection:
1,219 passed, 10 skipped. The separate optional MLflow observer suite was not
run locally because its optional dependencies are unavailable. Scoped and full
repository Ruff lint/format checks passed. The new retention wiring also passed
server-side Tekton admission. Three reviewers independently checked the source
and the earlier completed pair. Live visibility and retention were verified on
the failed infrastructure path and the final complete retained pair above.
