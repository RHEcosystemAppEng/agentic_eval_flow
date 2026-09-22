# Manual Trigger Guide -- AEH OpenShell, Harbor, ASE & A2A

Quick reference for manually triggering evaluations against the CI and monitoring pipelines.

---

## AEH OpenShell CI in an existing Forge SAW namespace

This procedure assumes the user has already deployed Forge SAW successfully in
`[NAMESPACE]`. Reuse its agent gateway, image, providers and certificate authority.
The evaluation profile is `abevalflow-pipeline-openshell`: prepare → evaluate →
analyze → store, followed by the cleanup finally Task. It omits the test cube.
The commands below are operator instructions; reading this guide does not deploy anything.

### 1. Choose the namespace and matching source revisions

Run from an Agentic Eval Flow checkout containing the native-mTLS fixes. The
older TLS bridge implementation is not the validated configuration. Use an
isolated checkout if your current checkout has local changes. Requirements:
authenticated `oc`, OpenShift Pipelines, Python 3 with PyYAML, a writable storage
class, sufficient quota, and permission to manage resources in your namespace.

```bash
export NS="[NAMESPACE]" # replace this literal before running
export FLOW_REV="[FLOW_REVISION]" # branch/tag containing the fixes, or commit SHA
export HARNESS_REV="[HARNESS_REVISION]" # OpenShell-capable AEH fork revision
oc get namespace "$NS"
oc -n "$NS" get vm,svc,pvc
oc -n "$NS" get endpointslices -l kubernetes.io/service-name=openshell-saw-agent-gateway
export GATEWAY_IP=$(oc -n "$NS" get svc openshell-saw-agent-gateway -o jsonpath='{.spec.clusterIP}')
```

At the time of the fixes, both repositories used `feat/aeh-openshell-openclaw`
(AEH repository: `GuyZivRH/agent-eval-harness`). Use `main` only after the relevant
changes are merged. Pin a tested commit for reproducibility. A branch reference
clones its current tip when a new run starts; an existing run is not updated.

There are two independently versioned layers: installed Tekton Pipeline/Task
YAML and Git-cloned Python scripts/submission files. Reapplying the matching
Task YAML is required after Task changes; changing a run's Git revision alone
does not update the installed Tasks.

### 2. Provision CI dependencies and namespace-local credentials

Forge SAW does not by itself provision the CI results stack. Before running,
provision PostgreSQL (`config/storage/postgres.yaml`), MinIO
(`config/storage/minio.yaml`), MLflow (`config/mlflow/`), and the judge's LiteLLM
service (`config/litellm/`) if they are absent. Render their namespace, internal
URLs, storage class, credentials and model upstream for your installation first.
Do not apply sample Secret values or overwrite an existing Forge deployment.

The expected service ports are PostgreSQL 5432, MinIO S3 9000 (console 9001),
MLflow 5000 and LiteLLM 4000. The source manifests contain deployment-specific
namespaces and endpoints: `oc -n` does not override `metadata.namespace`,
RoleBinding subject namespaces, or embedded DNS names.

Required Secret contracts, all in `[NAMESPACE]`:

- `openshell-gateway-mtls`: `ca.crt`, `tls.crt`, `tls.key`. The client leaf must
  be trusted by the running gateway and authorized for sandbox/provider operations.
  Preserve Forge's existing CA and server/client bundle; never generate an
  unrelated CI CA. The Forge bootstrap may also keep `server.crt`/`server.key` here.
- `forge-agent-upstream-tls`: `ca.crt` trusted by the agent's Forge upstreams.
- `inference`: `api_key`, required by the evaluate Task. Configure the actual
  model credentials in namespace Secrets and Forge providers.
- `openshell-credentials`: `M365_ACCESS_TOKEN`, `M365_USER`; optional refresh
  configuration `M365_TENANT_ID`, `M365_CLIENT_ID`, `M365_CLIENT_SECRET`.
  With real values already exported, run
  `EVAL_NS="$NS" ./config/forge-saw/create-openshell-credentials.sh`.
- `minio-credentials`: `endpoint-url` (for example
  `http://minio.[NAMESPACE].svc.cluster.local:9000`), `root-user`, `root-password`.
- `ab-eval-db-credentials`: `database-url` for the CI results database and
  `postgres-password` if using the checked-in PostgreSQL deployment. Its default
  user/database are `abevalflow`; keep the URL and database password consistent.
  Apply Alembic migrations, including `005_widen_eval_engine`, to this database
  before storage. `evaluation_runs` belongs to this database, not MLflow's schema.
- Private Git repositories additionally require Git credentials on the pipeline
  ServiceAccount. `github-token` key `token` is optional for publication features.

`openshell-oidc-credentials` is optional for this native-mTLS path. A successful
Keycloak login alone does not supply the TLS client certificate required by the
gateway. Kubernetes admin privileges also do not grant OpenShell application roles.

For MLflow, enable artifact serving (`mlflow-artifacts:/`) so remote clients do
not try to write the server's filesystem path locally. Configure its backend and
artifact destination deliberately: the basic checked-in deployment uses a PVC;
an S3-backed deployment needs MinIO credentials and network access. Allow the
namespace Service hostname in MLflow's host allowlist. Give MLflow sufficient
memory and compatible client/server versions; validate trace export, not only HTTP.

### 3. Apply the namespace networking fixes

Inspect both layers before editing:

```bash
oc -n "$NS" get networkpolicy
oc -n "$NS" get egressfirewall -o yaml
oc -n "$NS" get svc -o wide
oc -n "$NS" get pods --show-labels
oc -n "$NS" get endpointslices -o wide
oc -n default get svc kubernetes -o wide
```

Add namespace-scoped NetworkPolicies using the actual Service backend selectors.
CI source pods must match `tekton.dev/pipelineRun` with `operator: Exists`.
A source `podSelector` without a `namespaceSelector` means the same namespace.
The validated connections are:

- CI → agent gateway TCP 17670, with matching ingress on the SAW agent VM launcher
  pods. Typical gateway labels are `app.kubernetes.io/name: openshell-saw-agent`
  and `vm.kubevirt.io/name: openshell-saw-agent`; verify them in your deployment.
- CI → LiteLLM TCP 4000, MLflow TCP 5000, PostgreSQL TCP 5432 and MinIO TCP 9000,
  each with matching destination ingress. A MinIO console login on 9001 does not
  prove that the store Task can upload on 9000.
- MLflow → PostgreSQL TCP 5432 and, for S3 artifacts, MinIO TCP 9000; allow the
  corresponding destination ingress as well.
- CI (including cleanup), MLflow and other clients need DNS UDP/TCP 53 to the
  cluster DNS service. Preserve Forge's DNS policy.
- Cleanup → Kubernetes API Service TCP 443 and actual API endpoint TCP 6443
  where the cluster uses it. The existing Forge bootstrap-only API policy does
  not necessarily select Tekton pods. Allow the pipeline ServiceAccount to
  get/list/delete its namespace PVCs; a timeout is not an RBAC denial.

For example, this adds only gateway ingress from same-namespace CI pods (edit
the destination labels if the service uses different ones):

```bash
oc -n "$NS" apply -f - <<'YAML'
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: aeh-gateway-from-ci
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: openshell-saw-agent
      vm.kubevirt.io/name: openshell-saw-agent
  policyTypes: [Ingress]
  ingress:
    - from:
        - podSelector:
            matchExpressions:
              - key: tekton.dev/pipelineRun
                operator: Exists
      ports:
        - protocol: TCP
          port: 17670
YAML
```

NetworkPolicies are additive: this rule does not revoke access granted by other
policies. Review existing policies if you require CI-only gateway access.

Add the matching CI egress policy; ingress alone is insufficient under default
deny. For VM networking, inspect the Service EndpointSlice and allow the required
Service `/32`, endpoint `/32` and, when applicable, VM node `/32` on 17670 in
addition to the pod selector. Discover these addresses per namespace rather
than copying a previous cluster's addresses.

An OVN EgressFirewall is an additional filter. Preserve its existing Forge rules
and insert the required allows **before** its terminal Deny rules. For this CI,
HTTPS TCP 443 must reach `github.com`, `release-assets.githubusercontent.com`,
`pypi.org`, `files.pythonhosted.org`, and the configured model upstream. The
gateway's image-pull path also needs `ghcr.io` and its registry blob destinations
(including `pkg-containers.githubusercontent.com` and
`github-registry-files.githubusercontent.com` when used). Preserve the deployment's
Graph, identity-provider and other approved Forge destinations.

DNS-name rules alone did not consistently resolve the CDN timeouts in the
reference installation. Inspect resolution from a CI pod and the actual redirect
destination; add approved current IPv4 `/32` rules on TCP 443 when necessary.
Record and maintain those CIDRs in deployment configuration because CDN IPs change.
Allow the discovered gateway Service/endpoints and API addresses at this layer
too when blocked. Do not copy old cluster IPs or replace the namespace firewall
with allow-all. Persist additions in the owning Forge deployment configuration
so a Helm redeploy does not remove them.

### 4. Verify the existing gateway's native mTLS and runtime

The validated CLI setup stages the client bundle in
`$XDG_CONFIG_HOME/openshell/gateways/ci-gateway/mtls/`, registers via
`openshell gateway add ENDPOINT --local --name ci-gateway`, and restages the
bundle afterward. The corrected evaluate Task already does this. It unsets
`OPENSHELL_GATEWAY_INSECURE`; keep certificate verification enabled.

The working Forge server certificate includes `host.containers.internal`.
Use `https://host.containers.internal:17670` and a PipelineRun `hostAliases`
mapping to **your** `$GATEWAY_IP`. Verify your server SAN before using that name.
This alias is for CI pods; the inner sandbox's host bridge is a separate network.
An external OpenShift Route is not required for this in-cluster connection.

If certificates change on service restart, inspect the gateway's systemd unit
and cloud-init configuration on the SAW VM. Remove unconditional
`ExecStartPre=... generate-certs` regeneration through the deployment source,
preserve the existing matching certificates, and configure the service to load
them. Do not reset the state database or rotate the CA as a routine CI setup step.
Compare the served certificate and trust chain to the mounted client bundle
after any service restart. Use the deployment's actual unit name and TLS paths.

Before a full run, validate from a pod with the same CI label, Secret mounts and
host alias: an authenticated OpenShell operation, temporary sandbox creation,
readable image-owned `AGENTS.md` and skill `SKILL.md` files, and an actual GLM
response. A TCP connection or working Forge web chat does not prove this CI path.
Use the normal Forge startup/profile and providers; a bare `sleep infinity`
container does not validate the agent runtime.

The validated image is the GHCR digest in the PipelineRun template, not a custom
`sandbox-paths` image or mutable `latest`. Forge stages `/opt/forge` and
`/opt/openclaw` beneath `/sandbox/persist/.forge-image-runtime/`; inspect the
resulting workspace skill paths too. The `forge-image` mode uses image-owned
persona/skills. Preserve the Forge filesystem policy and existing providers
`forge-ai-gateway,m365-read-intervm,slack-read-proxy,drafts-service-agent`.
Agent GLM access uses these providers; the judge separately calls LiteLLM from
the Tekton pod. Validate both paths and the upstream CA.

### 5. Render and install the matching Pipeline and Tasks

The example below renders only the required Tekton resources and RBAC into a
private temporary directory. It also renders the PipelineRun's namespace DNS
and gateway alias. Run this from the checkout of `$FLOW_REV`, not an older checkout.
It requires PyYAML. Review the files before applying.

```bash
export CI_RENDER_DIR=$(mktemp -d)
python3 - <<'PY'
import json, os, pathlib, yaml
ns = os.environ['NS']
assert ns and '[' not in ns, 'Replace [NAMESPACE] first'
out = pathlib.Path(os.environ['CI_RENDER_DIR'])
files = ['config/rbac.yaml',
         'pipeline/tasks/phases/prepare.yaml',
         'pipeline/tasks/phases/evaluate.yaml',
         'pipeline/tasks/post/analyze-and-check-degradation.yaml',
         'pipeline/tasks/post/store.yaml', 'pipeline/tasks/post/cleanup_pvc.yaml',
         'pipeline/pipelines/ci-pipeline-openshell.yaml']
items = []
for path in files:
    text = pathlib.Path(path).read_text()
    for old in ('ab-eval-flow', 'gz-forge-eval'):
        text = text.replace(old, ns)
    for obj in yaml.safe_load_all(text):
        if obj:
            assert obj['kind'] in ('Task', 'Pipeline', 'ServiceAccount', 'Role', 'RoleBinding')
            obj.setdefault('metadata', {})['namespace'] = ns
            items.append(obj)
(out / 'resources.json').write_text(json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': items}))
run = yaml.safe_load(pathlib.Path('pipeline/runs/openshell-openclaw-pipelinerun.yaml').read_text())
run['metadata']['namespace'] = ns
overrides = {
    'revision': os.environ['FLOW_REV'], 'pipeline-repo-revision': os.environ['FLOW_REV'],
    'agent-eval-harness-repo-revision': os.environ['HARNESS_REV'],
    'openshell-gateway-endpoint': 'https://host.containers.internal:17670',
    'openshell-mtls-secret': 'openshell-gateway-mtls',
    'openshell-ai-gateway-ca-secret': 'forge-agent-upstream-tls',
    'llm-api-base': f'http://litellm.{ns}.svc.cluster.local:4000',
    'llm-base-url': f'http://litellm.{ns}.svc.cluster.local:4000/v1',
    'mlflow-tracking-uri': f'http://abevalflow-mlflow.{ns}.svc.cluster.local:5000',
}
params = {p['name']: p['value'] for p in run['spec']['params']}
params.update(overrides)
run['spec']['params'] = [{'name': k, 'value': v} for k, v in params.items()]
run['spec']['taskRunTemplate']['podTemplate']['hostAliases'] = [
    {'ip': os.environ['GATEWAY_IP'], 'hostnames': ['host.containers.internal']}]
(out / 'run.json').write_text(json.dumps(run, indent=2))
print(out)
PY
oc -n "$NS" apply --dry-run=server -f "$CI_RENDER_DIR/resources.json"
# Review resources.json and run.json, then install the namespaced resources.
oc -n "$NS" apply -f "$CI_RENDER_DIR/resources.json"
oc -n "$NS" auth can-i list persistentvolumeclaims --as="system:serviceaccount:$NS:pipeline"
oc -n "$NS" auth can-i delete persistentvolumeclaims --as="system:serviceaccount:$NS:pipeline"
oc -n "$NS" create --dry-run=server -f "$CI_RENDER_DIR/run.json"
```

The renderer does not provision Forge, Secrets, storage services, network policies
or database migrations; finish steps 2–4 first. If model names, providers or
Secret names differ, adjust the rendered params. `llm-api-key=mock` is valid only
when your LiteLLM explicitly accepts that value; configure real credentials
through Secrets otherwise. Keep the template's GHCR image digest and GLM model
overrides only if they match the deployment you validated.

### 6. Trigger, inspect and confirm completion

```bash
RUN=$(oc -n "$NS" create -f "$CI_RENDER_DIR/run.json" -o jsonpath='{.metadata.name}')
tkn -n "$NS" pipelinerun logs "$RUN" -f
oc -n "$NS" get pipelinerun "$RUN" -o wide
```

Prepare clones the submission and validates it. Evaluate's
`step-aeh-openshell-eval` runs the agent and judges. CI cases and scorers come from
`submissions/openclaw-forge/eval.yaml` (`dataset.path: cases`, `judges:`) and
`cases/{analysis-panel,morning-briefing}/{input,annotations}.yaml` in the chosen
submission revision. The harness demo bootstrap script is not run by CI.
For a fast first run, commit a submission variant whose `dataset.path` contains
only `analysis-panel` and select that revision. There is no case-filter parameter
in this Pipeline profile. If 900 seconds is insufficient, set
`execution.timeout: 1800` in the selected submission's eval.yaml; Task and
Pipeline timeouts must also leave room for scoring and publication.

Success requires evaluate green, authenticated gateway access, a real GLM
response, readable skills, judge scores without scoring errors, then successful
analyze/store and verified artifacts. Skipped unrelated engine steps are normal.
`response_received` alone is insufficient. Missing `/sandbox/output` needs the
agent logs/exit status checked; an empty directory is not a fix.

MLflow logging occurs inside evaluate. The store Task separately writes
`evaluation_runs` and uploads reports plus `debug/aeh/` artifacts to MinIO.
Confirm the DB row for this run and uploaded objects: a missing DB Secret can
cause store to skip database insertion. Trace counts/feedback must be nonzero
when events exist; fix incompatible MLflow packages or missing event collection
if trace export fails. Do not interpret a missing mean reward as a score of zero.

Keep each port-forward running in a separate terminal:

```bash
oc -n "$NS" port-forward svc/abevalflow-mlflow 15000:5000
# Another terminal, with NS set there too:
oc -n "$NS" port-forward svc/minio 9001:9001
```

Open MLflow at <http://127.0.0.1:15000> and select the experiment named after the
PipelineRun. Open MinIO at <http://127.0.0.1:9001>; use that namespace's MinIO
credentials. Connection refused usually means the local forward stopped. MinIO
9001 is the web console; application uploads use 9000.

Cleanup requires API connectivity even after evaluation succeeds. The corrected
Task retries and may defer cleanup with a warning; verify PVC deletion rather
than assuming green means it was deleted. If store fails, preserve its workspace
before cleanup and run a store-only TaskRun with that existing PVC and the
original run ID/results paths. A normal PipelineRun rerun executes evaluation
again; it does not resume at store.

Before any rerun, verify both Git revisions, installed Task versions, namespace
URLs and the current gateway Service IP. Service recreation can invalidate a
saved host alias. Helm redeployment can revert manual firewall or TLS changes;
retain the validated settings in the namespace's deployment source.


---

The remaining Harbor, ASE and A2A examples use their original `ab-eval-flow`
namespace. For a new Forge SAW installation, use `[NAMESPACE]` and the OpenShell
procedure above; those other engine examples are not OpenShell setup commands.

## A2A Monitoring Trigger Sources

A2A monitoring runs are triggered automatically by three sources (plus manual PipelineRuns):

| Source | When it fires | Agent mode | Key params |
|--------|---------------|------------|------------|
| **Quay push webhook** | New image pushed to `quay.io/ecosystem-appeng/google-lightspeed-agent` | Ephemeral deploy | `agent-image`, `agent-tag`; leave `agent-endpoint` empty |
| **LiteLLM config change** | Push to `main` on `RHEcosystemAppEng/agentic_eval_flow` modifying `config/litellm/*` | Existing deployed agent | `agent-endpoint` only |
| **10-day CronJob** | Scheduled every 10 days (`0 6 */10 * *`) | Existing deployed agent | `agent-endpoint` from canary pack config |

**Quay push:** EventListener trigger `quay-push-trigger` deploys a temporary instance of the pushed image, evaluates it, then cleans up. Requires a Quay repository notification pointing at the EventListener URL (pending Ilona access).

**LiteLLM config change:** EventListener trigger `litellm-config-push-trigger` re-tests against the existing agent at `http://lightspeed-agent.ab-eval-flow.svc:8000`. Requires a GitHub webhook on `RHEcosystemAppEng/agentic_eval_flow` pointing at the EventListener URL.

**10-day CronJob:** The `abevalflow-monitoring` CronJob performs a health check on `/.well-known/agent.json` before triggering; skips eval if the agent is unhealthy.

**Pre-flight health check:** Every A2A pipeline run includes an `a2a-pre-flight` step (between deploy and eval) that verifies `/.well-known/agent.json` returns HTTP 200. The run fails fast if the agent is unreachable.

**Manual A2A params:**
- **Existing agent:** set `agent-endpoint` (e.g. `http://lightspeed-agent.ab-eval-flow.svc:8000`)
- **Ephemeral deploy:** set `agent-image` + `agent-tag`, leave `agent-endpoint` empty

See [A2A Trigger Types](#a2a-trigger-types) below for full PipelineRun examples.

**Working examples -- Monitoring pipeline:**
- Harbor: https://console-openshift-console.apps.cn-ai-lab.2vn8.p1.openshiftapps.com/k8s/ns/ab-eval-flow/tekton.dev~v1~PipelineRun/harbor-verify-t6spp/logs?taskName=analyze-and-check-degradation
- ASE: https://console-openshift-console.apps.cn-ai-lab.2vn8.p1.openshiftapps.com/k8s/ns/ab-eval-flow/tekton.dev~v1~PipelineRun/ase-verify-sfmlw/logs?taskName=analyze-and-check-degradation
- A2A: https://console-openshift-console.apps.cn-ai-lab.2vn8.p1.openshiftapps.com/k8s/ns/ab-eval-flow/tekton.dev~v1~PipelineRun/a2a-local-env-x8nbj/logs?taskName=analyze-and-check-degradation

**Working examples -- CI pipeline:**
- Harbor (all files): https://console-openshift-console.apps.cn-ai-lab.2vn8.p1.openshiftapps.com/k8s/ns/ab-eval-flow/tekton.dev~v1~PipelineRun/ci-harbor-with-instr-p92vj
- Harbor (AI generation): https://console-openshift-console.apps.cn-ai-lab.2vn8.p1.openshiftapps.com/k8s/ns/ab-eval-flow/tekton.dev~v1~PipelineRun/ci-harbor-gen-instr-mn425
- ASE (all files): https://console-openshift-console.apps.cn-ai-lab.2vn8.p1.openshiftapps.com/k8s/ns/ab-eval-flow/tekton.dev~v1~PipelineRun/ci-ase-with-evals-7thhz
- ASE (AI generation): https://console-openshift-console.apps.cn-ai-lab.2vn8.p1.openshiftapps.com/k8s/ns/ab-eval-flow/tekton.dev~v1~PipelineRun/ci-ase-gen-evals-89p6d
- A2A: https://console-openshift-console.apps.cn-ai-lab.2vn8.p1.openshiftapps.com/k8s/ns/ab-eval-flow/tekton.dev~v1~PipelineRun/ci-a2a-qqjfp

---

## Prerequisites

- `oc` CLI logged in to the cluster (`oc whoami` should return your user)
- Namespace: `ab-eval-flow`

---

# Monitoring Pipeline (`abevalflow-monitoring-pipeline`)

Runs evaluations with degradation check and Slack notifications. No security scan or quality review.

## Harbor Run (hello-world)

Uses `skill-submissions/hello-world` -- a minimal Harbor task submission.

```bash
oc create -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: harbor-verify-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-monitoring-pipeline
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/skill-submissions.git"
    - name: revision
      value: "main"
    - name: submission-dir
      value: "hello-world"
    - name: eval-engine
      value: "harbor"
    - name: pipeline-repo-revision
      value: "main"
    - name: llm-model
      value: "claude-sonnet"
    - name: llm-api-base
      value: "http://litellm.ab-eval-flow.svc:4000"
    - name: llm-api-key
      value: "mock"
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
YAML
```

**Expected result:** Treatment and control both score 1.000, recommendation `pass`.

---

## ASE Run (hello-world-full)

Uses `skill-submissions/hello-world-full` -- a skill submission with `evals.json`.

```bash
oc create -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: ase-verify-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-monitoring-pipeline
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/skill-submissions.git"
    - name: revision
      value: "eval/hello-world-full"
    - name: submission-dir
      value: "hello-world-full"
    - name: eval-engine
      value: "ase"
    - name: pipeline-repo-revision
      value: "main"
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
YAML
```

**Expected result:** Score 1.000, recommendation `pass`.

---

## A2A Run (lightspeed-agent)

Uses `Agentic Eval Flow/a2a-agent-eval` -- evaluates the deployed Lightspeed A2A agent.
The agent must already be running at `http://lightspeed-agent.ab-eval-flow.svc:8000`.

```bash
oc create -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: a2a-verify-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-monitoring-pipeline
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/agentic_eval_flow.git"
    - name: revision
      value: "main"
    - name: submission-dir
      value: "a2a-agent-eval"
    - name: eval-engine
      value: "a2a"
    - name: pipeline-repo-revision
      value: "main"
    - name: agent-endpoint
      value: "http://lightspeed-agent.ab-eval-flow.svc:8000"
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
YAML
```

**Expected result:** 3/3 trials pass, mean reward 1.000.

---

## A2A Trigger Types

A2A monitoring runs can be triggered three ways (plus manual runs). Choose the right agent deployment mode for each:

| Trigger | Agent deployment | Key params |
|---------|------------------|------------|
| Quay push webhook | Ephemeral (new image) | `agent-image` + `agent-tag` |
| LiteLLM config change | Existing deployed agent | `agent-endpoint` |
| 10-day scheduled | Existing deployed agent | `agent-endpoint` |
| Manual PipelineRun | Either mode | See examples below |

**Ephemeral deploy** (`agent-image` + `agent-tag`): The pipeline deploys a temporary instance of the pushed image, evaluates it, then cleans up. Leave `agent-endpoint` empty.

**Existing agent** (`agent-endpoint`): The pipeline connects to an already-running agent (typically `http://lightspeed-agent.ab-eval-flow.svc:8000`). Do not set `agent-image`/`agent-tag` unless you want a fresh deploy.

### 1. Quay Push Webhook

Fires when a new image is pushed to `quay.io/ecosystem-appeng/google-lightspeed-agent` (excluding `sha256:` digest tags and `on-pr-*` PR tags).

- EventListener trigger: `quay-push-trigger` in `event-listener.yaml`
- Deploys an ephemeral instance of the new image for testing, then cleans up
- **Pending:** Ilona to configure the Quay repository notification pointing at the EventListener URL

### 2. LiteLLM Config Change

Fires when any file under `config/litellm/` (e.g., `config/litellm/configmap.yaml`) is pushed to the `main` branch of `RHEcosystemAppEng/agentic_eval_flow`.

- EventListener trigger: `litellm-config-push-trigger`
- Uses the existing deployed agent at `http://lightspeed-agent.ab-eval-flow.svc:8000`
- **Requires:** GitHub webhook on the Agentic Eval Flow repo pointing to:
  `https://el-submission-listener-ab-eval-flow.apps.cn-ai-lab.2vn8.p1.openshiftapps.com`

### 3. 10-Day Scheduled

Fires via the `abevalflow-monitoring` CronJob every 10 days (`schedule: "0 6 */10 * *"`).

- Uses the existing deployed `google-lightspeed-agent` endpoint from the canary pack config
- Performs a health check on `/.well-known/agent.json` before triggering; skips eval if unhealthy

### Manual Trigger (A2A)

Example PipelineRun for an A2A monitoring run against the existing deployed agent:

```bash
oc create -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: a2a-monitoring-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-monitoring-pipeline
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/agentic_eval_flow.git"
    - name: revision
      value: "main"
    - name: submission-dir
      value: "a2a-agent-eval"
    - name: eval-engine
      value: "a2a"
    - name: pipeline-repo-revision
      value: "main"
    - name: agent-endpoint
      value: "http://lightspeed-agent.ab-eval-flow.svc:8000"
  timeouts:
    pipeline: "2h"
    tasks: "1h30m"
  taskRunTemplate:
    serviceAccountName: pipeline
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
YAML
```

Example for ephemeral deploy (Quay webhook equivalent -- evaluates a specific image tag):

```bash
oc create -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: a2a-ephemeral-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-monitoring-pipeline
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/agentic_eval_flow.git"
    - name: revision
      value: "main"
    - name: submission-dir
      value: "a2a-agent-eval"
    - name: eval-engine
      value: "a2a"
    - name: pipeline-repo-revision
      value: "main"
    - name: agent-image
      value: "quay.io/ecosystem-appeng/google-lightspeed-agent"
    - name: agent-tag
      value: "<tag-from-quay-push>"
    - name: agent-endpoint
      value: ""
  timeouts:
    pipeline: "2h"
    tasks: "1h30m"
  taskRunTemplate:
    serviceAccountName: pipeline
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
YAML
```

---

# CI Pipeline (`abevalflow-pipeline`)

Full evaluation run: prepare → test → evaluate → analyze → store. No degradation check or Slack.
Security scan and quality review can be enabled/disabled per run.

## CI Harbor -- all files present (no generation)

Uses `skill-submissions/hello-world` -- has `instruction.md` and `tests/`. Generation is enabled
but skips because files already exist. Expects no `generated/` folder in MinIO.

```bash
oc create -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: ci-harbor-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-pipeline
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/skill-submissions.git"
    - name: revision
      value: "main"
    - name: submission-dir
      value: "hello-world"
    - name: eval-engine
      value: "harbor"
    - name: pipeline-repo-revision
      value: "main"
    - name: enable-generation
      value: "true"
    - name: enable-security-scan
      value: "false"
    - name: enable-quality-review
      value: "false"
    - name: llm-model
      value: "claude-sonnet"
    - name: llm-api-base
      value: "http://litellm.ab-eval-flow.svc:4000"
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
YAML
```

**Expected result:** Score 1.000, no `generated/` folder in MinIO, `debug/` folder present.

---

## CI Harbor -- AI generation (no instruction.md)

Uses `skill-submissions/hello-world-no-instr` (PR #106) -- only has `skills/SKILL.md` and
`metadata.yaml` with `generation_mode: ai`. Pipeline generates `instruction.md`,
`test_outputs.py`, and `llm_judge.py`.

```bash
oc create -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: ci-harbor-gen-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-pipeline
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/skill-submissions.git"
    - name: revision
      value: "eval/hello-world-no-instr"
    - name: submission-dir
      value: "hello-world-no-instr"
    - name: eval-engine
      value: "harbor"
    - name: pipeline-repo-revision
      value: "main"
    - name: enable-generation
      value: "true"
    - name: enable-security-scan
      value: "false"
    - name: enable-quality-review
      value: "true"
    - name: llm-model
      value: "claude-sonnet"
    - name: llm-api-base
      value: "http://litellm.ab-eval-flow.svc:4000"
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
YAML
```

**Expected result:** `generated/` folder in MinIO with AI-header files, quality review score reported.

---

## CI ASE -- all files present (no generation)

Uses `skill-submissions/hello-world-full` -- has `evals/evals.json`. Expects no `generated/` folder.

```bash
oc create -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: ci-ase-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-pipeline
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/skill-submissions.git"
    - name: revision
      value: "eval/hello-world-full"
    - name: submission-dir
      value: "hello-world-full"
    - name: eval-engine
      value: "ase"
    - name: pipeline-repo-revision
      value: "main"
    - name: enable-generation
      value: "true"
    - name: enable-security-scan
      value: "false"
    - name: enable-quality-review
      value: "false"
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
YAML
```

**Expected result:** Score 1.000, no `generated/` folder in MinIO.

---

## CI ASE -- AI generation (no evals.json)

Uses `skill-submissions/hello-world-minimal` (PR #104) -- only has `SKILL.md`. Pipeline generates
`evals/evals.json` with `_generated_by: "ai"`.

```bash
oc create -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: ci-ase-gen-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-pipeline
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/skill-submissions.git"
    - name: revision
      value: "eval/hello-world-minimal"
    - name: submission-dir
      value: "hello-world-minimal"
    - name: eval-engine
      value: "ase"
    - name: pipeline-repo-revision
      value: "main"
    - name: enable-generation
      value: "true"
    - name: enable-security-scan
      value: "false"
    - name: enable-quality-review
      value: "false"
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
YAML
```

**Expected result:** `generated/evals.json` in MinIO with `_generated_by: "ai"`.

---

## CI MCPChecker -- ExploitIQ

Uses `skill-submissions/exploitiq-mcp-eval` (branch `test/mcpchecker-exploitiq`).
Requires `exploitiq-mcp-credentials` secret with a fresh `oc whoami -t` token from `ai-dev03`.

**Refresh token before running:**
```bash
oc login --token=<your-token> --server=https://api.ai-dev03.kni.syseng.devcluster.openshift.com:6443
NEW_TOKEN=$(oc whoami -t)
oc config use-context "ab-eval-flow/api-cn-ai-lab-2vn8-p1-openshiftapps-com:6443/gziv@redhat.com"
oc create secret generic exploitiq-mcp-credentials \
  --from-literal=MCP_URL="https://exploitiq-mcp-server-exploit-iq-testings.apps.ai-dev03.kni.syseng.devcluster.openshift.com/mcp" \
  --from-literal=MCP_BEARER_TOKEN="$NEW_TOKEN" \
  -n ab-eval-flow --dry-run=client -o yaml | oc apply -f -
```

```bash
oc create -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: ci-mcp-exploitiq-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-pipeline
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/skill-submissions.git"
    - name: revision
      value: "test/mcpchecker-exploitiq"
    - name: submission-dir
      value: "exploitiq-mcp-eval"
    - name: eval-engine
      value: "mcpchecker"
    - name: pipeline-repo-revision
      value: "main"
    - name: enable-generation
      value: "false"
    - name: enable-security-scan
      value: "false"
    - name: enable-quality-review
      value: "false"
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
YAML
```

**Expected result:** All tasks pass (health_check, list_reports, analyze_cve).

---

## CI A2A

Uses `Agentic Eval Flow/a2a-agent-eval`. Security scan and quality review disabled (no test files).

```bash
oc create -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: ci-a2a-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-pipeline
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/agentic_eval_flow.git"
    - name: revision
      value: "main"
    - name: submission-dir
      value: "a2a-agent-eval"
    - name: eval-engine
      value: "a2a"
    - name: pipeline-repo-revision
      value: "main"
    - name: agent-endpoint
      value: "http://lightspeed-agent.ab-eval-flow.svc:8000"
    - name: security-scan
      value: "disabled"
    - name: enable-test-quality-review
      value: "false"
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
YAML
```

**Expected result:** Mean reward 1.000.

---

## Monitoring & Cleanup

```bash
# Watch all runs
oc get pipelinerun -n ab-eval-flow --watch

# Tail logs for a specific run/task
oc logs -n ab-eval-flow <run-name>-evaluate-pod -c step-harbor-eval -f
oc logs -n ab-eval-flow <run-name>-evaluate-pod -c step-ase-eval -f
oc logs -n ab-eval-flow <run-name>-evaluate-pod -c step-a2a-eval -f
oc logs -n ab-eval-flow <run-name>-analyze-and-check-degradation-pod -c step-check-degradation -f

# Delete all failed runs
oc get pipelinerun -n ab-eval-flow --no-headers | grep "False" \
  | awk '{print $1}' > /tmp/failed.txt && while read r; do oc delete pipelinerun -n ab-eval-flow "$r"; done < /tmp/failed.txt
```

---

## Slack Notifications

Every completed monitoring run sends a Slack message to the team channel:
- ✅ `[ENGINE] Monitoring Pass` -- score + baseline + ratio
- 🚨 `[ENGINE] Performance Degradation Detected` -- score dropped below threshold

The Run ID in the message is a clickable link to the OpenShift console.

---

## Active Image (Harbor/A2A examples only)

All eval steps (`harbor-eval`, `a2a-eval`) use `eval-base:local-env` -- built from
Harbor `feature/local-environment` branch with `claude-code` pre-installed.
This is the canonical image; do not revert to `:latest`.

---

## Common Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `monitor.py: No such file` | Old `pipeline-repo-revision` pointing to deleted branch | Ensure `pipeline-repo-revision: main` |
| `NonZeroAgentExitCodeError` in Harbor eval | Local registry `eval-base` out of sync with main registry | Re-run the skopeo sync (see `infrastructure_ops.md`) |
| `Generated files: []` despite generation running | LLM call failed silently | Check stderr output in logs (now shown on failure) |
| `generated/` folder missing despite AI generation | Files already existed (no AI header) or generation failed | Check `STEP-GENERATE-TESTS` logs for WARNING output |
| `debug/` folder missing in MinIO for Harbor runs | `results-dir` not passed to store task | Fixed in PR #32 -- ensure `pipeline-repo-revision: main` |
| Degradation shows `0.00% → 0.00%` | `store` runs after `check-degradation`; monitor reads old DB runs | Fixed: current score passed from `report.json` via `--current-score` |
| No Slack alert despite degradation | `\|\|` block caught exit code 1 from `monitor.py` | Fixed: only exit code 2 (error) is non-blocking now |
| Slack Run ID shows `None` | `--run-id` not passed to `monitor.py` | Fixed: `--run-id "$(params.pipeline-run-id)"` now wired in task |
