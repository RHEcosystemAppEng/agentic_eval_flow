# Persistence

## MinIO (Object Storage)

Reports and artifacts are uploaded to MinIO under a timestamped prefix:

```
s3://ab-eval-reports/YYYYMMDD_hhmmss_{submission}_{run-id}/
├── report.json              # Main evaluation report
├── report.md                # Human-readable report
├── scorecard.json           # Unified scorecard
├── security_scans/          # Security scan results
│   └── security-scan.json
├── generated/               # AI-generated artifacts
│   ├── instruction.md
│   └── test_outputs.py
├── scaffolded/              # Scaffolded configs and review
│   └── _ai_review.json
└── trials/                  # Per-trial artifacts (Harbor)
    ├── trial_001/
    │   ├── agent/
    │   └── verifier/
    └── ...
```

## PostgreSQL (Results Database)

Evaluation results are persisted for historical analysis and monitoring:

- **Script:** `scripts/store_results.py`
- **Data stored:**
  - Submission metadata
  - Per-trial results (Harbor/ASE)
  - Security scan findings
  - Aggregate statistics
  - Scorecard recommendation


## MCPChecker results

The normal and Konflux `store` Tasks persist MCPChecker reports through the
same `scripts/store_results.py` entry point. A `mcpchecker-report.json` selects
the `mcpchecker_results` and `mcpchecker_tasks` tables, preserving task scores,
tool calls, judge verdicts and named assertion reasons. Both passing and failing
evaluations are stored. Retrying the same pipeline run ID is idempotent.

MinIO receives `report.json`, `mcpchecker-report.json` and native outputs under
`debug/mcpchecker/` within the run's report prefix. Storage reads database and
object-storage credentials from the cluster Secrets configured in the Task.

Coverage: `tests/test_mcpchecker_pipeline.py` executes both Tasks' database
scripts against SQLite, including failed evaluations and retries. This is
component-level script integration with Git/package installation doubled;
cluster acceptance must additionally verify PostgreSQL rows and download the
MinIO objects to compare their contents with the source reports.


## Build and restore MinIO

The public MinIO registry image is unavailable. Build the repository's pinned
MinIO release from its official source commit into the OpenShift registry:

```sh
oc -n ab-eval-flow apply -f config/storage/minio-build.yaml
oc -n ab-eval-flow start-build minio-source --from-dir=containers/minio --follow
oc -n ab-eval-flow apply -f config/storage/minio.yaml
oc -n ab-eval-flow rollout status deployment/minio
```

The Containerfile pins upstream commit
`07c3a429bfed433e49018cb0f78a52145d4bedeb`
(`RELEASE.2025-09-07T16-13-09Z`). The Deployment reads the existing
`minio-credentials` Secret and mounts the existing `minio-data` claim. Do not
delete that claim when restoring the server. Preserve existing PVC provisioning
settings when applying configuration to an installed cluster; the storage class
must match the cluster. Source building is infrastructure setup, separate from
evaluation scoring and storage-script tests.
