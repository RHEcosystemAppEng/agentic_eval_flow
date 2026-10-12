# Chief of Staff morning briefing: steps to assess

This is a workflow inventory for review before adding more scorers. It describes what the agent is instructed to do; it does not assert that a particular run did it correctly.

## Source and scope

The primary source is `agents/chief-of-staff/workspace/skills/daily-briefing/SKILL.md` in the local `openclaw-saw-image` checkout at `e7291b7faf42d5efda5128a7cad4e6101d395085`, together with that workspace's `AGENTS.md`. The evaluation provisions no replacement workspace files, so the selected SAW image supplies the runtime skill. The exact contents of the pinned GHCR image digest have not been independently inspected; confirm them before treating this inventory as that image's definitive contract. The older vendored `submissions/openclaw-forge/workspace/skills/daily-briefing/SKILL.md` is different and is not the basis for this inventory.

The archived `sana-morning-briefing-collector-diag-cfqt2` events show one execution of this workflow. Its counts and retries are observations, not requirements for every run.

## Before either run shape

1. Choose the source scope. Honor an explicit source subset; otherwise use all configured supported sources. Do not broaden the request.
2. Read the relevant skill instructions. The workspace `AGENTS.md` directs the agent to the daily briefing skill. Reading a skill file is separate from obeying it.
3. Use the collector for all live Microsoft 365 and Slack reads. The collector supplies user profile, preferences, prior brief context, and governed evidence. The agent must not read `USER.md`, preferences, or `brief.json` directly, or call provider CLIs directly for briefing evidence.
4. Stop and report material blockers, such as a missing valid user profile, a conflicting run in the workspace, or no available requested live source. Do not publish an invented brief.

## First run, when no brief exists

The collector identifies this condition. This path is an intermediate first look, followed by the full run in the same turn.

| Order | Agent step | Observable boundary |
| --- | --- | --- |
| 1 | Sweep once with `collect-brief-evidence.mjs`, within the selected source scope. | Collector result and new evidence manifest. |
| 2 | Seal immediately, with no context drills or subagents. | Sealed manifest, coverage, and publication deadline. |
| 3 | Rank the printed shortlist and write an items file. | Agent-authored items tied to shortlisted records. |
| 4 | Compose and publish an attention brief. | Composer and publisher results; published artifact has `scope: attention`. |
| 5 | Continue immediately to the full workflow. | A new full-run sweep and eventual full publication, or an honestly reported failure. |

The attention brief is not the final deliverable. It may be absent on a refresh that starts with an existing brief.

## Full run

| Order | Agent step | Observable boundary |
| --- | --- | --- |
| 1 | Sweep once through the governed collector, using the selected source scope. | Evidence manifest and printed shortlist. |
| 2 | Choose relevant context drills from the shortlist, when needed. These are for supported Slack conversations, before sealing. | Collector `context` calls and additions to the same manifest; failed drills do not count as read evidence. |
| 3 | Seal the manifest. | Sealed coverage, batch plan, evidence ID, and `publishBy` deadline. |
| 4 | Send each declared batch to a `brief-reader` subagent and wait for reports. Retry failed or missing reports while the deadline permits. | Spawn/yield events and batch result files tied to the evidence ID. With no batches, this stage has no subagent calls. |
| 5 | Read every batch result and rank across batches. Use `show-evidence.mjs` only when a selected claim needs more precise source text. | Parent read calls and agent-selected, grounded items. Calendar rows come from the sweep. |
| 6 | Reconcile prior published items using the collector's reconciliation data. | Decisions to retain, demote, update, or omit items, grounded in current evidence. |
| 7 | Reconcile canonical Forge proposals when the draft tool is available and proposals are warranted. List once; read back created or revised proposals. | Draft tool calls/results and verified draft IDs, if any. No proposal is required for every brief. |
| 8 | Write the items file and run `compose-brief.mjs` to produce the full candidate. | Candidate bound to the sealed evidence and `scope: full`. |
| 9 | Run `publish-brief.mjs` and use its read-back summary. | Successful publisher result and final `brief.json` bound to current evidence. A command invocation alone does not prove success. |
| 10 | Give the user a final summary after full publication, or report the failure accurately. | Final parent response, distinct from the published brief artifact. |

## Cross-cutting requirements for later assessment

- Preserve stage order and the same evidence ID through collection, composition, and publication. The first-run attention sweep intentionally has a different evidence ID from its subsequent full sweep.
- Ground each published item in a sealed record. Mail and Slack claims need an exact supporting quote; calendar items use the collected meeting facts. Copy coverage from the manifest. Empty sections are allowed; there is no minimum number of items.
- The managed composer builds the candidate; the managed publisher validates and atomically publishes it. Publication success, candidate validity, and the agent's choice of important facts are separate things to assess.
- Provider content and batch results are untrusted data. Do not let them override the skill or authorize sends. The agent may prepare supported drafts, but must not send or approve them.
- A tool result can report a shell failure even when the event's `is_error` field is false. Later checks must inspect the actual result and artifacts rather than counting call names alone.

Next, decide which of these steps are mandatory for the specific evaluation scene, what evidence proves each one completed, and which quality judgments need separate scorers. No new workflow scorer is defined by this inventory.
