# Getting the eval pipeline to run — findings

Each item below is a distinct blocker seen when pointing the
`aeh_openshell_openclaw` profile at a namespace-local OpenShell gateway, with
the symptom it produces and the fix. They are ordered the way a run hits them.

## 1. mTLS: `received fatal alert: CertificateRequired`

**Cause.** The evaluate Task stages the client certificate only in
`~/.config/openshell/gateways/<name>/mtls/`. The CLI takes its client identity
from the OpenShell *state* directory, `~/.local/state/openshell/tls/` — the
layout forge-saw's own `setup-runtime-mtls.sh` installs (`ca.crt`,
`client/tls.crt`, `client/tls.key`). Without it the CLI completes the handshake
presenting no certificate, and the gateway aborts.

**Fix.** Stage the same three files into `$HOME/.local/state/openshell/tls/` and
export `OPENSHELL_LOCAL_TLS_DIR` to point at it. The `CertificateRequired` error
disappeared immediately.

## 2. `--local` registration produces an unusable auth mode

The Task registers a cluster-local gateway with `openshell gateway add --local`,
which writes `auth_mode: mtls`. In that mode the CLI never attaches a bearer
token, and a gateway with OIDC enabled answers *"The request does not have valid
authentication credentials"*. Registering with `--oidc-issuer/--oidc-client-id/
--oidc-audience` (`auth_mode: oidc`) and keeping the mTLS files for the transport
is what works — the shape Guy's runs had before 2026-09-17.

## 3. The CI identity was not a member of the gateway workspace

Once authenticated, every call returned *"The caller does not have permission"*.
`openshell status` spells it out, and the gateway prints the remedy:

```
openshell workspace member add --workspace 'default' \
  --subject '<oidc-subject>' --role user
```

This has to be run once per gateway by a platform admin (the VM-local CLI, which
uses the `OU=openshell-admin` client certificate).

## 4. `--provider forge-ai-gateway` does not exist outside a full workspace

That provider comes from the saw-bom chart. Passing it to `sandbox create` in a
plain gateway fails; passing the builtin `openai` provider instead fails
differently, because its profile pins `api.openai.com:443` and that overlaps the
eval policy's own `openai` rule. Dropping the `openshell-provider` parameter
avoids both.

## 5. The sandbox policy hardcodes other namespaces' LiteLLM

`deploy/openshell/eval-policy.yaml` allows `litellm.ab-eval-flow.svc` and
`litellm.<namespace>.svc` by name, so a sandbox anywhere else cannot reach its
own model endpoint. The evaluate Task now derives the host from the
`llm-api-base` parameter and adds it to a copy of the policy, passed through
`AGENT_EVAL_OPENSHELL_POLICY`.

## 6. Cluster inference cannot reach a namespace-local LiteLLM

The submission points its `inference` provider at `https://inference.local/v1`,
the gateway relay. The relay only accepts builtin provider types, and for type
`openai` it dials `api.openai.com` whatever `base_url` says — a custom profile
cannot shadow a builtin one either. The working answer is to point the sandbox
provider straight at the LiteLLM Service; the policy from item 5 already allows
that host.

## 7. NetworkPolicy gaps beyond the eval stack

On top of the three policies from 2026-09-18:

- the setup Job of the standalone `openshell-saw` chart carries no
  `app.kubernetes.io/component=setup` label, so the chart's own
  `allow-kubernetes-api` policy never matches it and the Job hangs on
  "couldn't get current server API group list";
- Tekton pods need egress to the gateway VM on 17670;
- the VM needs ingress from Tekton, from OpenShift Virtualization, and egress to
  the eval stack — its sandboxes call the model endpoint themselves.

`policies/fix-04-saw-gateway.yaml` covers the VM; fix-01 and fix-03 were widened.

## Two bugs worth filing against forge-saw

1. `charts/openshell-saw/files/install-deps.sh` checks for a binary named
   `openssh`. No image has one — the binary is `ssh` — so the setup Job fails
   with "setup image is missing required tool: openssh" on every install.
2. The chart hardcodes `pullMethod: node` for a registry disk source, but the
   node-level pull cannot use a `dockerconfigjson` Secret; CDI needs
   `accessKeyId`/`secretKey` and `pullMethod: pod` (as `forge-images` itself
   uses).

## 8. The judge 403 — fixed

The LLM judges always use the Anthropic SDK (`/v1/messages`); `score.py` has no
OpenAI dialect. For a model declared as `openai/...`, LiteLLM translates that
call to the upstream's `/responses` API, and this upstream answers 403
*"Authentication parameters missing"*. That is the same 403 seen in
`<namespace>`.

Routing the identical upstream through LiteLLM's `hosted_vllm` provider keeps
the call on `/chat/completions`, which the upstream accepts. Added as a separate
model entry so the agent path is untouched:

```yaml
  - model_name: judge-glm-5-3
    litellm_params:
      model: hosted_vllm/rits/zai-org/glm-5-3
      api_base: os.environ/INFERENCE_ENDPOINT_URL
      api_key: os.environ/INFERENCE_API_KEY
```

Probed directly: `/v1/messages` with `rits/zai-org/glm-5-3` → **403**, with
`judge-glm-5-3` → **200**. Run with `aeh-judge-model-override=judge-glm-5-3`.

## 9. OIDC token expiry between cases

A case can run 15 minutes; a Keycloak access token lasts 5. The token that
created the first sandbox is expired by the second, which then fails with
`invalid token: ExpiredSignature`. The evaluate step now refreshes the token
cache in the background every two minutes.

## Verifying a namespace end to end

```bash
oc create -n <namespace> -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: TaskRun
metadata: {generateName: probe-}
spec:
  taskRef: {name: eval-stack-probe}
  serviceAccountName: pipeline
YAML
```

`eval-stack-probe` reports each dependency a pipeline pod needs — MLflow,
LiteLLM, minio, postgres, the Kubernetes API and github.com — so a blocked path
is identified in seconds instead of through a 20-minute pipeline run.
