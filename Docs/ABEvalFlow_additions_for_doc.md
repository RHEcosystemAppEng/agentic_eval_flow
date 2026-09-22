# ABEvalFlow Doc — Additions for User-Facing Summary

Each section below includes a clear placement instruction.
Paste each block into the Word doc at the indicated location.

---

## ADDITION 5
### PLACE: Immediately after the Certification Levels table (the Foundational / Trusted / Certified table), before the paragraph starting "Levels are strictly hierarchical"

---

**What each level actually validates:**

Foundational — 5 checks:
- Submission structure: required files exist (SKILL.md, instruction.md, test files)
- Metadata compliance: metadata.yaml matches the required schema (name, engine, experiment config)
- Python syntax: test files compile without errors
- Content quality review: LLM judge scores the skill on clarity, test coverage, and coherence
- Basic security scan: no hardcoded secrets, dangerous imports, or code injection risks

Trusted — adds 2 checks on top of Foundational:
- Evaluation assets: test files are correctly formatted and compatible with the selected engine (Harbor, ASE, A2A, or MCPChecker)
- Functional validation: A/B evaluation confirms the skill measurably improves agent performance, with statistical significance (p-value)

Certified — adds 1 check on top of Trusted:
- Full behavioral evaluation: complete A/B comparison with uplift scoring, significance test, and pass rate above threshold enforced in block mode — the skill must demonstrably outperform the control baseline

---

## ADDITION 1
### PLACE: Immediately after the opening bullet list (Skills / MCP Servers / Agents), before the "Pipelines" section

---

### Why ABEvalFlow

AI artifacts — skills, MCP servers, and agents — are increasingly submitted to shared marketplaces and production platforms, but there is no standardized quality bar for what "good" means. ABEvalFlow is the CI/CD layer that enforces one: every artifact goes through automated security, quality, and behavioral gates before it reaches production, and the result is a certified scorecard that stakeholders can trust.

---

## ADDITION 2
### PLACE: After the "4. Analyze" stage, before the "5. Store" stage — as a new top-level section

---

### Certification Levels

Every pipeline run produces a certification level based on the combined gate results. Certification is the primary trust signal — it answers not just "did this pass?" but "how thoroughly was it validated?"

| Level | What Was Checked |
|---|---|
| **Foundational** | Structure and metadata are valid; submission follows the required format; no schema violations |
| **Trusted** | All of the above, plus: security scan passed (no HIGH or CRITICAL findings); quality review score meets threshold |
| **Certified** | All of the above, plus: behavioral evaluation passed in block mode — the artifact demonstrably improves agent performance above the control baseline |

Levels are strictly hierarchical. A Certified artifact has satisfied every check for Foundational and Trusted as well. If a lower level fails, higher levels are automatically failed regardless of their individual gate results — there is no way to achieve Trusted without first achieving Foundational.

The highest achieved level is recorded in the scorecard, persisted to PostgreSQL, and published to Red Hat Compass.

---

## ADDITION 3
### PLACE: Immediately after the "Certification Levels" section above (Addition 2), before "5. Store"

---

### What a Submission Looks Like From the Outside

1. A developer pushes a skill, MCP server, or agent to the submissions repository
2. The pipeline triggers automatically via a git webhook
3. All gates run in sequence:
   - **Security** — scans instruction files for prompt injection, credential access patterns, and obfuscation; an optional LLM semantic review detects jailbreak attempts and description-behavior mismatches
   - **Quality** — an LLM judge reviews the skill definition and test coherence across five dimensions: clarity, coverage, coherence, feasibility, and robustness
   - **Behavioral** — the artifact is evaluated A/B style: 20 runs with the skill/agent active (treatment) vs. 20 runs without (control); uplift and statistical significance are computed
4. A unified scorecard is produced with gate scores, findings, a pass/warn/fail recommendation, and a certification level
5. Each gate result is pushed to Red Hat Compass as a Soundcheck fact — one fact per gate per artifact, structured as:
   - **Gate name** (evaluation / security / quality)
   - **Passed** (true/false)
   - **Score** (0.0–1.0)
   - **Mode** (warn or block)
   - **Evaluated at** (timestamp)
6. The certification level fact summarizes the highest tier achieved and which checks passed or failed at each level

No manual steps required after the initial push. The full audit trail — gate results, findings, certification history — is queryable from Compass and from the PostgreSQL results database.

---

## ADDITION 4
### PLACE: After the "Compass Facts Integration" section, before "Persistence"

---

### What's Next

| Area | Description |
|---|---|
| **Observability** | MLflow integration for LLM token and cost tracking per pipeline run; Grafana dashboards over normalized gate and certification data |
