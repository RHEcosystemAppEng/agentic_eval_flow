# Signed Component skill scores

Component evidence schema `2.0` records the same measured values used by the
six Compass skill facts. `build_skill_values` is shared by Compass publication
and `component_evidence`; neither database access nor enabled Compass publication
is needed to produce the values. PR builds also produce score evidence.

| Check ID | Source | Value type |
|---|---|---|
| `valid-skill-structure` | Dedicated structure validation | Boolean |
| `basic-security-validation` | `security_scan_score_100` | 0–100 |
| `content-quality-review` | `quality_scan_score_100` | 0–100 |
| `advanced-security-validation` | Same security score | 0–100 |
| `instruction-quality` | Same quality scan score | 0–100 |
| `enterprise-behavioral-evaluation` | `behavior_score_100` | 0–100 |

Numeric values use the existing one-decimal database rounding convention.
Behavior is the treatment mean reward, including pairwise evaluations; it is
not pairwise win rate or the authored evaluation gate's pass/fail score.
There is currently one security scan score and one quality scan score. Advanced
security and instruction quality apply their own thresholds to those sources.

Each `component-evaluation.json` evaluation has a `skill_facts` map containing
all six observations: `value`, `available`, `source`, `value_type`, check/schema
identity, run ID, source revision and evaluation time. Warning/failure verdicts
and existing behavioral metrics remain in the evidence. Scores are observations;
the managed market profile decides the passing thresholds.

Missing numeric evidence is `null, available: false`; a measured zero is
`0.0, available: true`. Structure remains Boolean and reports unavailable as
`false, available: false`. Scores from another run or revision are rejected.
An unrequested or unavailable behavior score is not taken from an older run.

## Chains result and full archive

`TEST_OUTPUT` retains the standard Konflux execution result. The existing
declared `PLUGIN_EVALUATION` result contains the Component identity, source and
payload digests, full evidence hash, and this compact score projection:

```json
{
  "schema_version": "2.0",
  "score_contract": "compass-skill-values/v1",
  "score_columns": [
    "valid-skill-structure",
    "security_scan_score_100",
    "quality_scan_score_100",
    "behavior_score_100"
  ],
  "skill_scores": {
    "rh-sre/cve-impact": [true, 90.0, 86.0, 85.0]
  }
}
```

The result still includes its existing identity/hash fields; the example above
shows only the score fields. Shared source scores are represented once instead
of duplicating values for the basic/advanced and content/instruction aliases.
`null` in a compact row denotes unavailable evidence. Full six-check records,
including exact availability and provenance, stay in the hash-bound report.

Chains captures this declared task result in the existing build provenance
using the platform's configured
[SLSA provenance format](https://tekton.dev/docs/chains/slsa-provenance/).
Readiness first runs native Conforma verification, retrieves the report artifact
from that verified provenance, validates its hash and package identity, then
compares full observations with the inline attested score projection. It does
not use current Compass or PostgreSQL scores to qualify an older build.

The producer uses compact JSON and checks a portable
[task-result size budget](https://tekton.dev/docs/installation/additional-configs/#enabling-larger-results-using-sidecar-logs).
Oversized score maps fail explicitly; scores are never truncated or omitted.
The 13-skill real-report replay produced a 1,372-byte score result and 2,471 bytes
across result files. That replay is a test fixture, not new signed evidence.

## Market policy

Profiles use `required_skill_checks` with inclusive `minimum` values or an exact
Boolean `equals: true`. Only the listed checks are required; all six observations
are still recorded. The internal profile requires structure, security >= 60 and
quality >= 60. Disabled Tier 2 markets additionally require advanced security
>= 90, instruction quality >= 70 and behavior >= 50.

Warnings are retained for review. A completed scanner with warnings can satisfy
a required score threshold. Scanner hard failures, execution errors, missing
observations, invalid types and wrong identities remain blocking. Required
behavior also needs completed execution, enough trials and zero trial errors;
its treatment score is authoritative over an authored/pairwise gate verdict.

Image/source/payload binding, complete skill coverage, provenance trust,
market opt-in, ownership, lifecycle, freshness and external SoundChecks remain
separate requirements. Only `redhat-only` readiness is active for this POC.

Existing schema-1 evidence remains readable for legacy use but cannot satisfy
score-based qualification. A fresh trusted build with the updated runner is
required; republishing Compass facts cannot add scores to an old attestation.
