# Infrastructure & Operations Guide

Deployment and operations reference for running Agentic Eval Flow on OpenShift.

## Prerequisites

- OpenShift cluster with Pipelines operator (Tekton) installed
- `oc` CLI authenticated with cluster-admin or namespace-admin
- `tkn` CLI (optional, for manual pipeline triggers and PipelineRun cleanup)

## Quay.io images (org registry)

Published Agentic Eval Flow images live under **`quay.io/ecosystem-appeng/`** (not a personal namespace):

| Image / bundle | Use |
|---|---|
| `abevalflow-redteam`, `abevalflow-pyrit` | Classic + Konflux red-team Task steps |
| `abevalflow-eval-base` | Konflux evaluate default (classic CI uses the OpenShift internal `eval-base` ImageStream instead) |
| `abevalflow-task-*` | Tekton Bundles for Konflux IntegrationTestScenario |

**CI push:** GitHub Actions (`push-bundles`) authenticates with robot account `ecosystem-appeng+abevalflow` via repo secrets `QUAY_USERNAME` / `QUAY_TOKEN`. Local pushes use the same robot (`podman login quay.io` then `make bundles` in `pipeline/integration`).

**Cluster pull (`ab-eval-flow`):** Keep a `quay-pull-secret` dockerconfig secret for `quay.io` with that robot, and attach it to the Tekton `pipeline` ServiceAccount `imagePullSecrets` so red-team / Quay-backed steps can pull if repos are private. Classic Harbor/AEH evaluate steps continue to use `image-registry.openshift-image-registry.svc:5000/ab-eval-flow/eval-base:*` and do not depend on Quay.

## Namespace Setup

```bash
oc new-project ab-eval-flow --description="Agentic Eval Flow A/B evaluation pipeline"
```

## Deployment Order

Apply manifests in this order to satisfy dependencies:

```bash
# 1. RBAC -- ServiceAccount, Roles, RoleBindings
oc apply -f config/rbac.yaml

# 2. Security -- resource quotas
oc apply -f config/security/resource_quota.yaml

# 3. Network policies -- choose ONE based on LLM mode (see below)
oc apply -f config/security/network_policy_default_deny.yaml
oc apply -f config/security/network_policy_<mode>.yaml

# 4. Storage -- workspace and dead-letter PVCs
oc apply -f config/storage/workspace_pvc.yaml
oc apply -f config/storage/dead_letter_pvc.yaml

# 5. Cleanup -- create ConfigMap from script, then apply CronJob
oc create configmap cleanup-script \
  --from-file=cleanup.sh=scripts/cleanup.sh \
  -n ab-eval-flow --dry-run=client -o yaml | oc apply -f -
oc apply -f config/storage/cleanup_cronjob.yaml

# 6. Tekton tasks
oc apply -f pipeline/tasks/

# 7. Tekton triggers
oc apply -f pipeline/triggers/

# 8. Expose EventListener
oc create route edge el-submission-listener \
  --service=el-submission-listener \
  --port=http-listener

# 9. (Optional) LiteLLM -- only for Vertex AI mode
#    Creates a dedicated litellm ServiceAccount, Deployment, Service, and ConfigMap.
#    Requires the litellm-credentials Secret (see LiteLLM Setup below).
oc apply -f config/litellm/
```

## Network Policy Selection

Choose the network policy that matches your LLM access mode. Always
apply the default-deny policy first, then add the mode-specific allow
policy.

| LLM Mode | Policies to Apply | Effect |
|---|---|---|
| Direct API key | `default_deny` + `direct_api` | Trial pods can reach provider HTTPS endpoints + DNS |
| Vertex AI + LiteLLM | `default_deny` + `litellm` | Trial pods can only reach in-cluster LiteLLM on port 4000 |
| Self-hosted model | `default_deny` + `self_hosted` | Trial pods can only reach in-cluster model server |

Trial pods must carry the label `abevalflow/role: trial` for policies
to take effect. The Harbor fork's `OpenShiftEnvironment` should set
this label when creating trial pods.

## LiteLLM Setup (Vertex AI Mode Only)

1. Create the credentials secret with your GCP service account key:

```bash
oc create secret generic litellm-credentials \
  --from-file=GOOGLE_APPLICATION_CREDENTIALS_JSON=path/to/sa-key.json \
  --from-literal=LITELLM_MASTER_KEY=$(openssl rand -hex 32) \
  -n ab-eval-flow
```

2. Edit `config/litellm/configmap.yaml` to set your GCP project and
   model routing.

3. Apply the manifests:

```bash
oc apply -f config/litellm/
```

4. Verify the proxy is healthy:

```bash
oc get pods -l app.kubernetes.io/name=litellm -n ab-eval-flow
oc port-forward svc/litellm 4000:4000 -n ab-eval-flow &
curl http://localhost:4000/health
```

## Storage

| PVC | Purpose | Default Size |
|---|---|---|
| `abevalflow-workspace` | Shared pipeline workspace (source, builds, results) | 5Gi |
| `abevalflow-dead-letter` | Reserved for failed-run artifacts (manual use for now) | 2Gi |

Adjust sizes based on expected submission volume and image sizes.

## MLflow (optional AEH observability)

Two layers share the same tracking server:

- **AEH** (`skills/eval-mlflow/scripts/log_results.py`, `/eval-mlflow`): judge
  metrics, `summary.yaml` / `report.html`, reconstructed traces. Configured by
  `eval.yaml` `mlflow.experiment` and `MLFLOW_TRACKING_URI` (do not hardcode the
  in-cluster URI in eval.yaml — local AEH uses localhost or an env override).
- **CI** (`scripts/log_aeh_mlflow.py` after evaluate): invokes that same AEH
  script from the harness clone, patches experiment to the Tekton PipelineRun
  name, and falls back to a minimal metrics log if AEH no-ops.

Harbor / default pipelines: `enable-mlflow=false`, empty `mlflow-tracking-uri`.
`abevalflow-pipeline-openshell` defaults both **on**
(`http://abevalflow-mlflow.ab-eval-flow.svc.cluster.local:5000`). See
`config/mlflow/README.md` for security notes (in-cluster hosts only; Route is
optional/dev-only; SQLite is not HA).

```bash
# Deploy tracking server (PVC uses cluster default StorageClass)
oc apply -f config/mlflow/pvc.yaml
oc apply -f config/mlflow/service.yaml
oc apply -f config/mlflow/deployment.yaml

# Pipeline params (example)
#   enable-mlflow: "true"
#   mlflow-tracking-uri: http://abevalflow-mlflow.ab-eval-flow.svc.cluster.local:5000
```

Do **not** apply `config/mlflow/route.yaml` unless you also append the Route
hostname to `--allowed-hosts` in `deployment.yaml`.

The AEH runner image (`containers/agent-eval-harness/Containerfile`) bakes
`mlflow-skinny` + `pandas`. The evaluate step still pip-installs to `/tmp` only
when those imports are missing (older images). Rebuild/push the AEH image after
changing that Containerfile.

## OpenShell gateway for OpenClaw evals

`eval-engine=aeh_openshell_openclaw` uses the **NVIDIA OpenShell already
installed** on this cluster (Helm release `openshell` in namespace `openshell`,
image `ghcr.io/nvidia/openshell/gateway`). Sandboxes are Kubernetes pods in
that namespace, not KubeVirt VMs. Evaluate does not install OpenShell; it only
calls:

```text
http://openshell.openshell.svc.cluster.local:8080
```

That Service is reachable from `[NAMESPACE]` (gRPC `:8080`, TLS disabled
on the current gateway.toml). Secret `openshell-credentials` (`M365_ACCESS_TOKEN`,
`M365_USER`, optional `M365_TENANT_ID` / `M365_CLIENT_ID` / `M365_CLIENT_SECRET`)
belongs in the PipelineRun namespace for Forge Graph evals. Evaluate fails
closed if those required keys are missing. `openshell-mtls` is optional while
the gateway has `disable_tls = true`.

For a deployment, substitute `[NAMESPACE]` for the PipelineRun namespace and
use the checked-in OpenShell profile. Before triggering CI, namespace-local network rules must allow the
pipeline's Kubernetes API access (`172.30.0.1/32` on TCP 443/6443), the
OpenShell gateway on TCP 17670, LiteLLM on 4000, PostgreSQL on 5432, MinIO on
9000, and the approved HTTPS destinations used for repository cloning,
dependency installation, and model access. These are namespace-scoped
NetworkPolicy/EgressFirewall changes; do not modify cluster-wide policy.

If using an external forge-saw/KubeVirt gateway instead, apply the matching
TCP 17670 ingress rule in the gateway namespace and the `[NAMESPACE]`
egress rule. Preserve the mTLS CA/client certificate pair and use a hostname
covered by the gateway certificate. Do not point the current cluster profile
at the old VM Route or disable certificate verification. Validate the endpoint
and a temporary sandbox before running the full CI evaluation.

forge-saw (KubeVirt VM on `:17670`) is a different OpenShell host for clusters
that run OpenShift Virtualization. Do not Helm-install it on this ROSA cluster.
See `config/forge-saw/README.md` only if you are targeting a CNV cluster.

Trigger example: [trigger_guide.md](trigger_guide.md) (`abevalflow-pipeline-openshell`, `eval-engine=aeh_openshell_openclaw`).

**Store:** cluster Postgres must have Alembic **005** (`evaluation_runs.eval_engine` varchar(50)) or the DB insert fails (`aeh_openshell_openclaw` is 22 characters). OpenShell artifacts reuse MinIO `debug/aeh/`; Harbor `_eval_tmp` debug is N/A.

## Cleanup CronJob

Runs daily at 03:00 UTC. Configurable via environment variables:

| Variable | Default | Description |
|---|---|---|
| `NAMESPACE` | `ab-eval-flow` | Target namespace |
| `POD_AGE_HOURS` | `24` | Delete completed/failed trial pods older than this |
| `PIPELINERUN_KEEP_COUNT` | `7` | Keep the N most recent PipelineRuns, delete the rest |

To run cleanup manually:

```bash
oc create job --from=cronjob/abevalflow-cleanup manual-cleanup -n ab-eval-flow
```

## Resource Quotas

The default quota (`config/security/resource_quota.yaml`) limits:

| Resource | Limit |
|---|---|
| Pods | 50 |
| CPU requests | 32 cores |
| Memory requests | 64Gi |
| CPU limits | 64 cores |
| Memory limits | 128Gi |
| PVCs | 10 |

Adjust based on cluster capacity and expected concurrency.

## Pod Security

Trial pods spawned by Harbor's `OpenShiftEnvironment` should follow the
security context documented in `config/security/pod_security_reference.yaml`:

- `runAsNonRoot: true`
- `allowPrivilegeEscalation: false`
- Drop all Linux capabilities
- Seccomp `RuntimeDefault`
- Resource requests/limits per trial pod

The Harbor fork currently sets `HOME=/tmp` instead of
`readOnlyRootFilesystem: true` for agent compatibility. This is
documented in `Docs/harbor_openshift_backend.md`.

## Failure Handling

See [failure_handling.md](failure_handling.md) for retry policies,
timeouts, dead-letter path, and partial-run recovery.

## Verification

After deploying, verify the infrastructure:

```bash
# Check ServiceAccount
oc get sa pipeline -n ab-eval-flow

# Check RBAC
oc auth can-i create pods --as=system:serviceaccount:ab-eval-flow:pipeline -n ab-eval-flow

# Check network policies
oc get networkpolicy -n ab-eval-flow

# Check PVCs
oc get pvc -n ab-eval-flow

# Check CronJob
oc get cronjob -n ab-eval-flow

# Check EventListener
oc get el,route -n ab-eval-flow

# Check resource quota usage
oc describe resourcequota eval-resource-quota -n ab-eval-flow
```
