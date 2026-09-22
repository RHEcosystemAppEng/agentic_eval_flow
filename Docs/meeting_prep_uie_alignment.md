# UIE Alignment Meeting Prep

> **Date:** 2026-06-30 (Tuesday)
> **Attendees:** Kendall, Mayur, Guy Ziv, others
> **Purpose:** Technical sync on ABEvalFlow ↔ Compass integration

---

## Summary Status

| Topic | Status | Notes |
|-------|--------|-------|
| Pipeline Deployment Ownership | ❓ Needs clarification | COMPASS-1050 tracking |
| Scorecard Definition | ✅ Largely resolved | Have AiResource schema |
| Facts → Compass Integration | ✅ Resolved | In `soundcheck-fact-producers` group |
| Skill Submission Git Workflow | ✅ Largely resolved | SKILL.md frontmatter, auto-discovery |
| Skill Packs Model | ✅ Resolved | Eval per skill, Compass aggregates for packs |

---

## 1. Pipeline Deployment Ownership ❓

**Related Jira:**
- [COMPASS-1049](https://redhat.atlassian.net/browse/COMPASS-1049) — Agentic Skills eval pipeline (Mayur Deshmukh, In Progress)
- [COMPASS-1050](https://redhat.atlassian.net/browse/COMPASS-1050) — Evaluate Compass IDP role in Skills eval pipeline (Kendall Totten, In Progress)

**What we know:**
- COMPASS-1050 is tracking the IDP role question
- Flow: GitLab CI triggers → ABEvalFlow runs eval → writes facts to Soundcheck

**Still need to clarify:**
- Who deploys the Tekton pipeline in the tenant cluster?
- Who maintains it post-deployment?
- We'll assist with setup, but need a clear owner on the UIE side

---

## 2. Scorecard Definition Alignment ✅

**Related Jira:**
- [COMPASS-1100](https://redhat.atlassian.net/browse/COMPASS-1100) — Add ability to ingest AI skills into Compass catalog (Mayur Deshmukh, In Progress)
- [COMPASS-1117](https://redhat.atlassian.net/browse/COMPASS-1117) — Schema explorations for Skills ingestion (To Do)

**Source Documents:**
- [Converging Catalog Entity Schemas for Compass, CMDB, InScope](https://docs.google.com/document/d/1-inB77UJ_V8MAH2Tf_XK1PFaaRDHOyPcSBmBrT7LfNY/edit?tab=t.ee55xobfdyww)
- [WIP Compass Entity Schema Spreadsheet](https://docs.google.com/spreadsheets/d/1DyaGjY1Je-ElpVWlgi8Oq3OzAluliKly_KOSMs6pPI4/edit?pli=1&gid=463335999#gid=463335999)

### AiResource (Skills) Schema

From the Compass Entity Schema spreadsheet:

```yaml
apiVersion: backstage.io/v1alpha1
kind: AiResource
metadata:
  name: frontend-design                    # Required - kebab-case machine name
  title: Frontend Design Skill             # Optional - human readable
  namespace: redhat                        # Optional - grouping
  description: Skill for creating...       # Optional
  tags: [web, design]                      # Optional
  annotations:
    backstage.io/source-location: "url:https://gitlab.cee.redhat.com/org/repo/-/blob/main/{path}/SKILL.md"  # Required!
    servicenow.com/appcode: CMPS-002       # Optional - CMDB link
    gitlab.com/project-slug: org/repo      # Optional
    backstage.io/techdocs-ref: dir:.       # Optional
  links: []                                # Optional
  labels:
    distribution: internal                 # Required: internal | external | internal-external
spec:
  type: skill                              # Required - only "skill" supported now
  owner: group:redhat/team-name            # Required - user or group ref
  lifecycle: production                    # Required: alpha | beta | ga
  system: system:compass/compass-platform  # Optional - parent system
  disciplines: [web, backend]              # Optional - engineering areas
  categories: [framework, security]        # Optional - descriptive tags
  agents: [claude-code, opencode]          # Optional - tested AI tools
  dependsOn: [api:redhat/some-mcp-server]  # Optional - dependencies
```

### Required Fields

| Field | Type | Values |
|-------|------|--------|
| `metadata.name` | kebab-case | Unique machine name |
| `metadata.annotations.backstage.io/source-location` | URL | Path to SKILL.md |
| `metadata.labels.distribution` | enum | `internal` / `external` / `internal-external` |
| `spec.type` | enum | `skill` (only option for now) |
| `spec.owner` | entity ref | `group:redhat/<group>` or `user:redhat/<uid>` |
| `spec.lifecycle` | enum | `alpha` / `beta` / `ga` |

### What ABEvalFlow Should Validate

1. Source location points to valid SKILL.md
2. Owner exists in Compass
3. Lifecycle matches distribution expectations
4. Dependencies resolve (if declared)
5. Quality/safety checks from eval pipeline

### Remaining Question

- What specific **scorecard checks/rules** map to these fields?
- Where is the "Skill scorecard" rules source of truth?

---

## 3. Facts → Compass Integration ✅

**Related Jira:**
- [COMPASS-1138](https://redhat.atlassian.net/browse/COMPASS-1138) — Create a new RBAC group for soundcheck facts api (Verified)

**Confirmed:
- RBAC group `soundcheck-fact-producers` exists
- Members: `compass-devs`, `dmartino`, `gziv`
- Purpose: Allow skill eval pipeline to submit facts to Soundcheck

**Flow:**
```
ABEvalFlow Pipeline
       ↓
  Writes facts to external fact source
       ↓
  Soundcheck reads facts
       ↓
  Computes pass/fail per check
       ↓
  Results surface on entity's scorecard in Compass
```

**To validate:**
- Need deployed environment to test end-to-end fact push
- Can't fully validate locally

---

## 4. Skill Submission Git Workflow ✅

**Related Jira:**
- [COMPASS-1186](https://redhat.atlassian.net/browse/COMPASS-1186) — Implement auto-discovery of AI Skills in the Compass Catalog (New)
- [COMPASS-1106](https://redhat.atlassian.net/browse/COMPASS-1106) — Explore - Skills catalog ingestion process (Closed)

**Source Document:**
- [User Journey: Sharing an Internal Agentic Skill](https://redhat.atlassian.net/wiki/spaces/UIE/pages/402951887)

### Key Findings

From the User Journey document:

> "A custom processor/provider can be pointed at a GitLab repo and will **scan for SKILL.md files**, and **auto-generate catalog entities**. No separate catalog-info.yaml needed per skill."

### Workflow

1. Skill creator writes `SKILL.md` with frontmatter (per [agentskills.io/specification](https://agentskills.io/specification#frontmatter))
2. Custom provider scans repo for SKILL.md files
3. Auto-generates AiResource entities from frontmatter
4. Entities appear in Compass catalog
5. Eval pipeline triggers on changes
6. Results pushed as facts to Soundcheck

### Ingestion Options

- **Repository URL** — Provider scans entire repo
- **lola-market.yaml** — Explicit list of skills

### Reference Repos

- `gitlab.cee.redhat.com/ixd-firefly/ixd-firefly-custom-skills` — Example with catalog.yaml
- `github.com/RHEcosystemAppEng/agentic-collections` — Our skills repo

### Remaining Question

- Which repo is the **production** skills registry for UIE?
- Is `ixd-firefly-custom-skills` the canonical source, or is there another?

---

## 5. Skill Packs Discussion ✅ (Resolved 2026-06-30)

**Source:** Slack thread between Guy Ziv, Daniele Martinoli (Lightforge), and mdeshmuk (Compass)

### Published Docs

AI Skills documentation is now live in Compass:
- https://compass.redhat.com/docs/compass/system/compass-platform/ai-hub/skills/

### Agreed Resolutions

| Question | Resolution | Owner |
|----------|------------|-------|
| Evaluation scope | **Individual skill level** | ABEvalFlow |
| Pack aggregation | **Done in Compass**, not ABEvalFlow | Compass |
| Trigger model | **Per skill** | Caller/GitLab CI |
| Pack/skill relationships | **Compass tracks the mapping** | Compass |

### Key Quotes from mdeshmuk

> "I think that can be managed from Compass. We can send pack level scorecard, or skill level. And we can also include links and references of each skill in each manifest."

> "Skill level fact is perfect. We can perform the aggregation for the packs in Compass."

> "I'm partial towards triggers per skill."

> "Compass can take care of the relations between skills and packs/plugins. So the pipeline does not have to track the mapping."

### Implications for ABEvalFlow

1. **No change needed** — ABEvalFlow remains a single-artifact evaluation pipeline
2. **Skill-level facts only** — Continue pushing facts per skill; Compass aggregates for packs
3. **No pack awareness required** — Pipeline doesn't need to know about packs or relationships
4. **Trigger responsibility** — Caller (GitLab CI or orchestrator) triggers one run per skill

### Design Principle

> **ABEvalFlow = evaluation engine, not orchestration layer**
>
> The pipeline evaluates ONE artifact (skill/agent/MCP server). Pack iteration and aggregation happen outside the pipeline — either in the caller or in Compass.

### Daniele's Note

From distribution perspective, per-skill scans are enough. Related UX issue raised: #73 Interactive context selection at install time for multi-context modules (separate from eval).

### Skill Pack Registration (for reference)

When a pack is registered:
1. Each skill in the pack gets its own AiResource (auto-generated)
2. Pack entity tracks relationships to its skills
3. Compass can show aggregate results from individual skill facts
4. No special handling needed from ABEvalFlow

---

## Related Jira Tickets

| Ticket | Summary | Status | Assignee |
|--------|---------|--------|----------|
| [COMPASS-1049](https://redhat.atlassian.net/browse/COMPASS-1049) | Agentic Skills eval pipeline | In Progress | Mayur Deshmukh |
| [COMPASS-1050](https://redhat.atlassian.net/browse/COMPASS-1050) | Evaluate Compass IDP role in Skills eval pipeline | In Progress | Kendall Totten |
| [COMPASS-1100](https://redhat.atlassian.net/browse/COMPASS-1100) | Ingest AI skills into Compass catalog | In Progress | Mayur Deshmukh |
| [COMPASS-1138](https://redhat.atlassian.net/browse/COMPASS-1138) | RBAC group for soundcheck facts api | Verified | — |
| [COMPASS-1117](https://redhat.atlassian.net/browse/COMPASS-1117) | Schema explorations for Skills ingestion | To Do | — |
| [COMPASS-1186](https://redhat.atlassian.net/browse/COMPASS-1186) | Auto-discovery of AI Skills | New | — |
| [APPENG-4926](https://redhat.atlassian.net/browse/APPENG-4926) | Ingest skills from Compass Skill Registry | New | — |

---

## Meeting Summary (Post-Discussion)

### Resolved Items

| Topic | Resolution |
|-------|------------|
| ✅ Facts API | In `soundcheck-fact-producers` group, ready to push facts |
| ✅ AiResource schema | Full spec from Compass Entity Schema spreadsheet |
| ✅ Skill registration | SKILL.md frontmatter, auto-discovery, no catalog-info.yaml |
| ✅ Skill Packs | Eval per skill, Compass aggregates; pipeline doesn't track packs |
| ✅ Trigger model | Per skill (not per pack) |

### Still Open

| Topic | Status |
|-------|--------|
| ❓ Pipeline ownership | Who deploys/maintains in tenant cluster? (COMPASS-1050 tracking) |
| ❓ Scorecard rules | Which specific checks map to AiResource fields? |
| ❓ Production repo | Final skills registry location? |

---

## References

- [Converging Catalog Entity Schemas for Compass, CMDB, InScope](https://docs.google.com/document/d/1-inB77UJ_V8MAH2Tf_XK1PFaaRDHOyPcSBmBrT7LfNY/edit?tab=t.ee55xobfdyww)
- [WIP Compass Entity Schema Spreadsheet](https://docs.google.com/spreadsheets/d/1DyaGjY1Je-ElpVWlgi8Oq3OzAluliKly_KOSMs6pPI4/edit?pli=1&gid=463335999#gid=463335999)
- [User Journey: Sharing an Internal Agentic Skill](https://redhat.atlassian.net/wiki/spaces/UIE/pages/402951887)
- [agentskills.io specification](https://agentskills.io/specification#frontmatter)
- [APPENG-4926: Ingest skills from Compass Skill Registry](https://redhat.atlassian.net/browse/APPENG-4926)
- [Compass AI Skills Docs](https://compass.redhat.com/docs/compass/system/compass-platform/ai-hub/skills/) — Published 2026-06-30
