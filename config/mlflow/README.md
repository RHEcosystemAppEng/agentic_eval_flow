# Agentic Eval Flow MLflow (eval-only)

Lightweight tracking server for AEH post-evaluate logging. **Not** production
telemetry HA — single replica, SQLite on a PVC.

## Apply (in-cluster CI)

```bash
oc apply -f config/mlflow/pvc.yaml
oc apply -f config/mlflow/service.yaml
oc apply -f config/mlflow/deployment.yaml
```

Pipeline tracking URI (ClusterIP):

```text
http://abevalflow-mlflow.ab-eval-flow.svc.cluster.local:5000
```

Set pipeline params `enable-mlflow=true` and `mlflow-tracking-uri` to that URL
(`abevalflow-pipeline-openshell` already defaults both). That URI is exported
as `MLFLOW_TRACKING_URI` for the AEH harness during evaluate, then
`scripts/log_aeh_mlflow.py` calls AEH `log_results.py` (and a CI fallback).

Do **not** put this ClusterIP in `eval.yaml` `mlflow.tracking_uri` — local
`/eval-mlflow` would then try to reach in-cluster DNS. Leave experiment in
yaml (`forge-eval-rubrics`); CI overrides the experiment name to the
PipelineRun id so cluster runs do not collide.

Client packages (`mlflow-skinny`, `pandas`) are baked into
`containers/agent-eval-harness/Containerfile`. Evaluate still pip-installs to
`/tmp` only when those imports are missing (older images).

## Security defaults

- `--allowed-hosts` lists in-cluster Service DNS + localhost only (no `*`).
- CORS wildcards are **not** enabled; browser UIs need an explicit allowlist.
- `route.yaml` is **optional / dev-only**. Do not apply it unless you also
  append the Route hostname to `--allowed-hosts` in `deployment.yaml`.

## Storage

`pvc.yaml` omits `storageClassName` so the cluster default applies. Uncomment
`storageClassName: gp3` (or your class) in that file if you need a specific
provisioner.
