# Compass skill fact alignment — 2026-11-05

The evaluator now publishes six explicit facts for Compass Stage's existing
`ai-skill-quality` track. The track keeps its check IDs; its check rules read
`$.value` and require `$.available == true`. Numeric thresholds use inclusive
comparisons on a 0–100 scale.

| Check / fact reference suffix | Source | cve-impact replay | Compass requirement | Result |
|---|---|---|---|---|
| `valid-skill-structure` | Dedicated structure validation | `true` | `true` | Passed |
| `basic-security-validation` | `security_scan_score_100` | 90.0 | >= 60 | Passed |
| `content-quality-review` | `quality_scan_score_100` | 86.0 | >= 60 | Passed |
| `advanced-security-validation` | `security_scan_score_100` | 90.0 | >= 90 | Passed |
| `instruction-quality` | `quality_scan_score_100` | 86.0 | >= 70 | Passed |
| `enterprise-behavioral-evaluation` | `behavior_score_100` | 85.0 | >= 50 | Passed |

Fact references use the submission's configured prefix; this deployment uses
`ai5-marketplace:default/`. The replay entity was
`airesource:ai5-marketplace/cve-impact`.

## What changed

The former structure rule read `skill-structure-validation` at `$.valid`, while
the evaluator published `valid-skill-structure` at `$.passed`. Advanced security
read the obsolete `security_skillmd-scanner` fact. Content quality read the LLM
submission review rather than the quality scan in the database scorecard.
Instruction quality checked the scanner's boolean gate verdict. Behavior read
the evaluation gate score, which can have a different meaning in pairwise runs.

The updated rules consume dedicated fact identities and explicit scalar values.
Basic and advanced security share a source score with different thresholds.
Content and instruction quality share the quality scan score; there is no
separate instruction scoring evaluator today. Behavior reads the treatment mean
reward used by the database's `behavior_score_100`, rather than the pairwise win
rate. Numeric scores use the database view's one-decimal rounding convention.

Every fact records its source, availability, evaluation run, source revision,
and evaluation time. `available` is an evidence flag, not a pass/fail verdict:
a measured score of 40 is available even when below a threshold. Missing numeric
evidence publishes `value: null, available: false` to replace stale values.
Structure always publishes a boolean; unavailable structure evidence has
`value: false, available: false`.

All fact requests now set `cache: true`. The live experiment confirmed that an
uncached HTTP 200 submission did not leave facts available for a later explicit
check execution. Cached submissions resolved correctly.

Existing gate facts (`passed` and 0–1 `score`), certification level facts, and the
certification summary continue to be published. The six track rules now use the
explicit skill facts. Per-fact publication outcomes are saved in
`scorecard.json` under `skill_fact_push_results`.

## Verification

This was a replay of real database evidence, not a new evaluation. The source
was the latest `cve-impact` scorecard for run
`rh-sre-on-push-6d9k8-evaluate-skill-b1564a9c19fb1fc7`, completed on
2026-11-04 at 15:25:31 UTC. The database was only read. Its pairwise win rate was
50.0, while `behavior_score_100` was 85.0; Compass correctly resolved 85.0.

The new fact builder generated the replay payload from that run's saved check
and score data. The payload was marked `publication_mode: database-replay` and
retained the original run, revision, and evaluation time. All six check rules
were updated in Compass Stage. Their subsequent execution and saved `/results`
responses both showed all six passing, with each `resolvedFactValue` matching
the table above.

Regression coverage compares HTTP fact payloads with the persisted
`latest_skill_scorecards` view, distinguishes behavior from pairwise scores,
checks unavailable evidence and publication failures, and ensures the checked-in
Compass rules reference the emitted fact identities and fields.

## Next real evaluation

The shared score builder also feeds the new
[signed Component score evidence](component-score-evidence.md). Compass and
attestation values now come from the same in-process observations; PR builds
produce the evidence even when fact publication is disabled.

The source changes are local and uncommitted. Rebuild the evaluator runner image
from these changes and update its pinned digest before the next real pipeline
evaluation. The six live Compass check rules are shared: other skills need a
publication from the updated evaluator before their new facts can be resolved.

The rule definitions are in
[compass-skill-checks.yaml](examples/compass-skill-checks.yaml). The full payload
and publication contract is documented in [persistence.md](persistence.md#compass-facts).
