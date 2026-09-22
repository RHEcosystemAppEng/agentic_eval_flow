# Shared forge-saw gateway for ABEvalFlow OpenClaw evals

Install **once**. Evaluate PipelineRuns only call
`python -m agent_eval.openshell.run` against the in-cluster gateway. They
never Helm-install SAW.

On this cluster the gateway namespace is the existing personal project
**`guy-ziv-evalflow`** (this login cannot create `forge-saw`). Helm still
needs OpenShift Virtualization; a namespace alone is not a SAW VM.

In-cluster URL for PipelineRuns in `guy-ziv-evalflow`:

```text
https://abeval-saw-gateway.guy-ziv-evalflow.svc.cluster.local:17670
```

`sandboxName` / Helm release: `abeval-saw` (19-character OpenShell limit).

Shared-cluster install (cluster-admin, dedicated NS) remains:

```text
SAW_NS=forge-saw EVAL_NS=ab-eval-flow VALUES_FILE=config/forge-saw/values-abeval.yaml \
  ./config/forge-saw/bootstrap.sh
```

## What this is (and is not)

| Use | Skip |
|---|---|
| forge-saw Option C (Helm), **one standalone VM**, `agent: openclaw`, `containerRuntime: podman` | Helm/Keycloak/VM create from evaluate |
| In-cluster Service DNS from Tekton | Depending on the public TLS Route (gRPC passthrough) |
| AEH file-auth (`$M365_*`) into the inner sandbox | Ansible two-VM / ArgoCD Validated Pattern in this first pass |

This repo does **not** vendor [rh-forge/forge-saw](https://github.com/rh-forge/forge-saw). `bootstrap.sh` clones a pin.

## Cluster prerequisites

- OpenShift Virtualization: `VirtualMachine` API (`oc api-resources --api-group=kubevirt.io`)
- Red Hat Build of Keycloak operator (stable-v24) for forge-saw Option C
- Helm 3, `oc`, `virtctl`, `openshell` CLI on the bootstrap workstation
- OpenShift **4.22+** for current SAW charts (older clusters fail install)
- Capacity: values-abeval.yaml is 4 cores / 8 GiB / 40 GiB; `values-abeval-evalflow.yaml` is 2 / 4Gi / 40Gi on `gp3` to fit `guy-ziv-evalflow` quota

`bootstrap.sh` exits before Helm if the VirtualMachine API is missing.

## Sequence

1. Target an existing namespace (`SAW_NS=guy-ziv-evalflow` by default)
2. Clone forge-saw (private; workstation `gh` auth)
3. In that clone: `make check-prereqs`, Keycloak, golden image — still required
4. `make generate-keys` per forge-saw Option C
5. Helm install `charts/openshell-saw` with `values-abeval-evalflow.yaml`
6. Wait for the VM + setup Job. Gateway Service port `17670`
7. Copy gateway mTLS client material off the VM into Secret `openshell-mtls` in **the PipelineRun namespace** (same NS here). `OPENSHELL_GATEWAY_INSECURE=true` is last-resort only
8. Apply NetworkPolicy (ingress 17670 from `EVAL_NS`)
9. Pull credentials so the VM can pull `quay.io/aipcc/base-images/agentic/openclaw:...`

Wrapper:

```bash
# Existing namespace you can already `oc project` into — not from evaluate
./config/forge-saw/bootstrap.sh
```

Dedicated NS (cluster-admin):

```bash
SAW_NS=forge-saw EVAL_NS=ab-eval-flow \
  VALUES_FILE=./config/forge-saw/values-abeval.yaml \
  ./config/forge-saw/bootstrap.sh
```

Override pin / clone URL:

```bash
FORGE_SAW_PIN=<sha> FORGE_SAW_REPO=https://github.com/rh-forge/forge-saw.git \
  ./config/forge-saw/bootstrap.sh
```

## mTLS Secret (PipelineRun namespace)

After the VM is Ready, copy client certs (forge-saw documents `virtctl scp` of
`~/.config/openshell/gateways/.../mtls` or `~/.local/state/openshell/tls/`):

```bash
SANDBOX=abeval-saw
NS="${SAW_NS:-guy-ziv-evalflow}"
EVAL_NS="${EVAL_NS:-$NS}"
OUT=/tmp/abeval-saw-mtls
mkdir -p "$OUT"

for f in ca.crt tls.crt tls.key; do
  if [[ "$f" == "ca.crt" ]]; then
    src="/home/cloud-user/.local/state/openshell/tls/ca.crt"
  else
    src="/home/cloud-user/.local/state/openshell/tls/client/${f}"
  fi
  virtctl -n "$NS" scp \
    "cloud-user@vm/${SANDBOX}:${src}" "$OUT/$f" \
    --identity-file="${HOME}/.generated-ssh-keys/sandbox-ssh" \
    --local-ssh-opts=-oStrictHostKeyChecking=no \
    --local-ssh-opts=-oUserKnownHostsFile=/dev/null
done

oc create secret generic openshell-mtls -n "$EVAL_NS" \
  --from-file=ca.crt="$OUT/ca.crt" \
  --from-file=tls.crt="$OUT/tls.crt" \
  --from-file=tls.key="$OUT/tls.key" \
  --dry-run=client -o yaml | oc apply -f -
```

Evaluate mounts that Secret at `$HOME/.config/openshell` and rearranges files
into `gateways/abeval-saw/mtls/`.

Graph credentials (not in git). Forge OpenClaw needs a **real** token; the
placeholder YAML must not be applied:

```bash
# Preferred: export M365_ACCESS_TOKEN and M365_USER, then
./config/forge-saw/create-openshell-credentials.sh
```

Or:

```bash
oc create secret generic openshell-credentials -n guy-ziv-evalflow \
  --from-literal=M365_ACCESS_TOKEN=... \
  --from-literal=M365_USER=... \
  --from-literal=M365_TENANT_ID=... \
  --from-literal=M365_CLIENT_ID=... \
  --from-literal=M365_CLIENT_SECRET=...
```

Required: `M365_ACCESS_TOKEN`, `M365_USER`. Optional: tenant/client keys.
`M365_AUTH_HEADER_FILE` / `M365_GRAPH_CURL` are created in the sandbox from the
access token — do not put them in the Secret. Template (placeholders only):
`secret-openshell-credentials.yaml.template`.

Verify keys without dumping values:

```bash
oc get secret openshell-credentials -n guy-ziv-evalflow -o json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("\n".join(sorted((d.get("data") or {}))))'
```

## Preflight vs install

`aeh-openshell-eval` TCP-checks `:17670` and optionally runs `openshell sandbox list`.
If SAW is down the **run fails**. That step does not install SAW.

## Image

Stock `agent-eval-harness:v1.0.x` cannot import `agent_eval.openshell`. Point
`aeh-openshell-image` at an orchestrator image built from GuyZivRH
`agent-eval-harness` **main** that also includes the `openshell` CLI.
