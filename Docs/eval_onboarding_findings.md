# Morning briefing evaluation onboarding

This document describes the scorer development branch `sana/morning-brief-scorers-on-pr109`. The workflow source and its first-run/full-run distinction are in [Chief of Staff morning briefing steps](chief-of-staff-morning-briefing-steps.md). The exact skill inside the pinned SAW image has not been independently inspected; confirm it before treating the local image checkout as the definitive runtime contract.

## What this evaluation observes

- The scene in `submissions/openclaw-forge/scenes/monday-acquisition.yaml` names the test M365 account. Its mailbox is seeded outside this evaluation. Expected Watson/Contoso items must be checked against collected evidence before treating their absence as an agent recall failure.
- The agent's final chat response and Forge's published `brief.json` are separate outputs. On a first run, an attention brief may be published and then replaced by a full brief at the same path; only the last artifact remains. The event sequence is needed to assess the attention publication.
- Execution, full publication, content quality, and latency are separate results. An `exec` tool call is an attempt, not proof that its script succeeded. Match each call to its `tool_result` by `tool_use_id` and inspect the result or saved artifact.
- The morning-briefing quality judges in `submissions/openclaw-forge/eval.yaml` currently score the final response. They do not by themselves verify the content of the published cards.

## Workflow scorer inventory

Implement the **planned** checks one at a time after confirming which observations the harness reliably preserves. Existing checks should be reviewed for case scope and evidence limits. All full-brief checks are for the morning-briefing case, not the analysis-panel case.

| Scorer | Status and applicability | Entities and proof sought |
| --- | --- | --- |
| `used_catalog_skills` | Existing | In `outputs.events`, match `read` calls for the daily-briefing and Microsoft 365 `SKILL.md` paths to non-error `tool_result` entries by call ID. Reading is distinct from following the instructions. |
| `used_m365_tools` | Existing; case condition needs review | Match an `exec` call for `collect-brief-evidence.mjs` to its result; inspect saved sealed `brief.evidence.json` and `brief.index.json` for the expected account, retrieved M365 records, matching evidence IDs, and observed counts. Its current condition depends on `expected_top_of_mind`; that should be decoupled from disputed mailbox expectations. |
| `attention_brief_published` | Implemented for the current fresh-sandbox morning case | Pair the publisher call with its result; require its verified disk read-back to report `scope: attention` and an evidence ID. This check does not require the compose scorer. The final `brief.json` is replaced by the full run. |
| `continued_to_full_run` | Implemented for the current fresh-sandbox morning case | Require a later collector sweep with a matched full-run success result. If that result is redacted, require the collector manifest/index to agree on a new evidence ID and identify a previous brief. Attention publication and later composition are scored separately. |
| `full_evidence_sealed` | Implemented | Pair sweep and seal calls with results. Require the collector's explicit full-run seal result or matching sealed evidence/index files. A later compose result does not stand in for seal proof. |
| `full_batches_reviewed` | Implemented locally in `eval.yaml`; per sealed batch plan | For every declared batch, require an accepted `sessions_spawn` for `brief-reader` and a later parent `read` result with the matching batch and evidence IDs. Allow retries and empty selected-item lists. A sealed zero-batch plan passes without reader calls. |
| `full_candidate_composed` | Implemented locally in `eval.yaml` | Match `exec` for `compose-brief.mjs` to its success result reporting full composition for the sealed full-run evidence ID. A failed attempt followed by a successful retry can pass. This does not require later publication. |
| `full_publisher_succeeded` | Implemented locally in `eval.yaml` | Require an ID-matched publisher call after the current full-run seal, plus a saved full `brief.json` with the current evidence ID. Prefer the publisher's success/read-back result; when that result is redacted, the matching saved file supplies write evidence. Return a pass/fail reason that distinguishes no call, missing result, publisher error, missing file, and wrong saved scope/evidence. The saved artifact cannot independently attribute the write when the result is redacted. |
| `full_workflow_order` | Implemented locally in `eval.yaml` | Independently match successful full sweep and seal to the current evidence (using artifact timestamps when results are redacted), all declared reader batch results, successful full compose, and full publisher result in that order. Require the saved full brief for redacted publisher output. Allow retries and zero-batch plans; return the first stage not reached. |
| `published_brief` | Existing | Inspect saved `brief.json`, sealed evidence, and index for full scope, required Forge v1 structure, and matching evidence IDs. This verifies the final artifact; it does not prove which agent actions produced it or that its chosen items are useful. |
| `response_received` | Existing | Match the delivered `outputs.output_content` with the final parent assistant event; reject empty or `NO_REPLY` responses. This is distinct from publication. |

### Conditional work and quality

Slack context drills apply only when the selected source and relevant conversations call for them. Forge proposal list/create/revise/show operations depend on tool availability and whether a proposal is warranted. They should not be mandatory in every run. Attention checks are not applicable to a refresh that starts with an existing brief.

Separate quality scorers are needed for ranking, factual grounding, source quotes, and whether the chosen cards serve the executive. The workflow checks above answer whether the required steps completed, not whether the brief is good.

### Evidence gaps to settle before coding

The archived diagnostic `events.json` redacts some long tool results. In that run, the attention publisher's read-back is visible, while the full publisher's result is redacted. Three older archives lack saved collector manifest/index files, so the standalone continuation and seal checks return false on those archives for missing proof; this is not evidence that those steps failed. A scorer must not infer publisher success from `is_error: false` alone: an `exec` result can still contain a shell command failure. Confirm whether other retained output proves full publisher read-back; if it does not, capture a compact status artifact in a future run. Likewise, confirm that every batch result and the first-run/full-run signal is available to the scorer before requiring it. When interpreting a false boolean from an archived run, distinguish missing observations from a verified failed step; the current boolean interface does not encode that distinction.

## Implementation sequence

1. The attention publication, later full sweep, and full seal checks are implemented for the current fresh-sandbox morning case. Their check bodies are independent; when reused, adapt the case `if` condition to the target pipeline's run shape.
2. Batch review, full composition, publisher invocation/success, and core workflow ordering have now been implemented locally one at a time, with valid retries and zero-batch plans covered by focused tests.
3. Review existing scorer conditions, then develop content-quality and latency measures separately from workflow completion.

The focused scorer tests run with synthetic, source-free cases. To also run the
archived diagnostic assertions locally, set `MORNING_BRIEF_ARCHIVE` to that
case's `morning-briefing/` directory; CI skips those optional archive checks.

Do not start a new PipelineRun solely to test these definitions until the scorer and its required artifacts have been validated locally.
