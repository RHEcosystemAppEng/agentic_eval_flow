# Reports and persistence

Every run writes local evidence. Select publication explicitly with
`--persist postgres,s3,compass` or `ABEVAL_PERSIST`; credentials alone do not
enable uploads. Each destination is attempted independently, including after
failed checks. See the [CLI reference](cli.md#persistence-opt-in) for configuration.

## Destinations

| Destination | Configuration | Stored data |
|---|---|---|
| PostgreSQL | `ABEVAL_DATABASE_URL` | Evaluations, trials, scans, scorecards, certification, and review metrics. |
| S3 / MinIO | `ABEVAL_S3_ENDPOINT`, `ABEVAL_S3_BUCKET`, `ABEVAL_S3_ACCESS_KEY`, `ABEVAL_S3_SECRET_KEY` | Report files under `<skill>/<run-id>/reports/` and the manifest at `<skill>/<run-id>/run.json`. |
| Compass | `gate_policy.push_facts` in metadata; `ABEVAL_COMPASS_TOKEN` | Selected gate and certification facts. |

The base CLI installation writes local reports. Install the `storage` extra for
PostgreSQL/S3. Secret templates for
Kubernetes deployments are in [config/storage](../config/storage/).

## Reports

The run directory contains `run.json` and a `reports/` directory:

| Artifact | Contents |
|---|---|
| `run.json` | Configuration without credentials, source identity, step results, artifact paths, and publication outcomes. |
| `scorecard.json`, `scorecard.md` | Gate results and policy recommendation. |
| `certification.json` | Certification levels and individual checks. |
| `report.json`, `report.md` | Normalized AEH results when evaluation ran. |
| Scanner JSON/SARIF and logs | Available static evidence and scanner errors. |
| `_ai_review.json`, `skill-review.json` | Selected advisory reviews. |

Source copies and kubeconfigs are excluded from publication. Execution status,
policy verdict, and publication status are separate. Selected publication
failures fail the command by default; `--no-persistence-required` makes those
failures advisory.

## Retry publication

```sh
abeval publish --run-dir .abeval/runs/<run-id> --persist postgres,s3
```

Supply credentials again through the current environment. This command reuses
saved evidence; it does not repeat scans, reviews, or trials. PostgreSQL snapshots
are transactional and idempotent by run ID. S3 uses stable object keys.
A successful retry can still return a failure exit code from earlier run evidence.
Database initialization creates missing tables; it does not perform destructive
schema migrations.

## Compass facts

Gate policy selects fact identities and enabled facts. When certification
includes `valid_skill_structure`, Compass also receives a standalone
`<fact_ref_prefix>valid-skill-structure` fact with that check's own result,
independent of its certification level's overall status. Certification push
outcomes are recorded in `scorecard.json`.

## PostgreSQL TLS routes

Set `ABEVAL_DATABASE_TLS_ROUTE_HOST` (or `--database-tls-route-host` for a new
workflow) when PostgreSQL is exposed through a TLS passthrough route on port 443.
The database URL must use a loopback hostname. The CLI opens an ephemeral local
relay for the transaction and verifies the route certificate using the default
trust store or `SSL_CERT_FILE`. `DB_TLS_ROUTE_HOST` is an environment alias.
Direct database URLs do not start a relay.

## CI evidence

OCI archives and attachments are produced by the consuming Konflux task using
its existing task-runner tooling. They are independent of CLI persistence;
registry credentials and attachment status remain owned by the pipeline.
