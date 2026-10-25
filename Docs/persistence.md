# Reports and persistence

The shared task's `enable-persistence` parameter defaults to `false`.
When enabled, it aggregates available scan and AEH results, optionally pushes
Compass Facts, attaches reports to the caller's image, and stores configured
Postgres/MinIO outputs. Missing optional credentials skip that destination.

| Destination | Configuration | Contents |
|---|---|---|
| Compass Facts | `gate_policy.push_facts` plus `compass-facts-api/token` | Enabled gate facts and status |
| OCI artifact | Caller supplies `image-url` and `image-digest` | Report archive attached with ORAS |
| Postgres | `ab-eval-db-credentials/database-url` | Evaluation, trials, scans, scorecards, certification, review metrics |
| MinIO | `minio-credentials` | Reports, scanner outputs, AEH run artifacts, advisory review |

Compass also receives a standalone `<fact_ref_prefix>valid-skill-structure`
fact when the certification policy includes `valid_skill_structure`. For example,
the `ai5-marketplace:default/` prefix produces
`ai5-marketplace:default/valid-skill-structure`. Its data includes `check_id`,
`name`, `passed`, `score`, `message`, `source_gate`, `details`, and `evaluated_at`.
It uses the individual check result, independently of the foundational level's
overall status, and remains included in that level's existing `checks` array.
Push results are recorded in `scorecard.json` under
`certification_fact_push_results` with `level: valid-skill-structure`.

The Tier A `skill-structure-validation` step checks the skill's frontmatter,
naming, description, Apache-2.0 license, compatibility, allowed tools, line
count, directories, and content conventions. Errors fail the check; warnings
remain advisory. `skill-structure-validation.json` contains the result and
diagnostics used by certification. Missing or invalid evidence fails the check.

To produce the same report locally, run from this repository:

```sh
PYTHONPATH=. python scripts/validate_skill_structure.py /path/to/pack/skills/my-skill \
  --output reports/my-skill/skill-structure-validation.json
```

Postgres and MinIO secret examples live in `config/storage/`. Create them in the
namespace running the task and supply real endpoint/credential values. The task
also accepts the optional Postgres `database-tls-route-host` secret key.

`store_results.py` creates missing tables and uses the task run ID for
idempotency. It stores a scorecard on its own when no AEH evaluation ran.
Existing nullable provenance columns remain in the database model for deployed
schema compatibility; no destructive database migration runs during cleanup.

Reports use the submission metadata name when validation ran, otherwise the
catalog identifier. Storage prefixes use the existing timestamp/name/run-ID
shape. Core files include `report.json`, `report.md`, `scorecard.json`,
`certification.json`, and `skill-structure-validation.json`, when produced.
Scanner files live under `security_scans/`; AEH run files live under `debug/aeh/`.
Advisory review output uses the `scaffolded/ai_review.json` object key.

The OCI attachment uses artifact type
`application/vnd.agentic-eval.reports.v1`. Its task result is
`skill-reports-ARTIFACT_OUTPUTS`, containing `uri`, `digest`, and `skill` after a
successful attach. Pull it using:

```sh
oras pull --registry-config /path/to/auth.json -o ./reports "${uri}@${digest}"
```

Optional gate/LLM tracing uses `otel-exporter-endpoint`. OTLP dependencies are
baked into the task image. Advisory review also writes a token-metrics
checkpoint consumed by Postgres storage.
