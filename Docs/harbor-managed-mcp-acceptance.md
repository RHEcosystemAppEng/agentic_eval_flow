# Native Harbor managed-MCP acceptance evidence

## Final corrected run with retained logs

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
