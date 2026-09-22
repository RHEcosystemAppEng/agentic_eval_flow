#!/usr/bin/env bash
# One-time bootstrap of a shared forge-saw OpenShell gateway.
# Do NOT run this from the ABEvalFlow evaluate Task.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

FORGE_SAW_REPO="${FORGE_SAW_REPO:-https://github.com/rh-forge/forge-saw.git}"
# Pin from a known-good clone of rh-forge/forge-saw main (override as needed).
FORGE_SAW_PIN="${FORGE_SAW_PIN:-09e51c5e1bcb1d4f5cb599a0d933251929c65cd0}"
SAW_NS="${SAW_NS:-guy-ziv-evalflow}"
EVAL_NS="${EVAL_NS:-$SAW_NS}"
RELEASE="${RELEASE:-abeval-saw}"
CLONE_DIR="${CLONE_DIR:-${TMPDIR:-/tmp}/forge-saw-${FORGE_SAW_PIN}}"
if [[ -z "${VALUES_FILE:-}" ]]; then
  if [[ "$SAW_NS" == "guy-ziv-evalflow" ]]; then
    VALUES_FILE="$SCRIPT_DIR/values-abeval-evalflow.yaml"
  else
    VALUES_FILE="$SCRIPT_DIR/values-abeval.yaml"
  fi
fi
NETWORKPOLICY_FILE="${NETWORKPOLICY_FILE:-$SCRIPT_DIR/networkpolicy-gateway-from-abeval.yaml}"
WAIT_TIMEOUT="${WAIT_TIMEOUT:-30m}"

usage() {
  cat <<EOF
Usage: $0 [--skip-helm] [--skip-clone]

Installs one standalone OpenClaw SAW VM into namespace ${SAW_NS}.
Evaluate PipelineRuns only talk to the gateway; they never Helm-install SAW.

Requires OpenShift Virtualization (VirtualMachine API). An existing
namespace is enough — this script does not need to create projects.

Environment:
  FORGE_SAW_REPO   Git URL (default: ${FORGE_SAW_REPO})
  FORGE_SAW_PIN    Commit SHA to check out
  SAW_NS           Target namespace (default: guy-ziv-evalflow)
  EVAL_NS          Namespace that runs evaluate (default: same as SAW_NS)
  RELEASE          Helm release / sandboxName (default: abeval-saw)
  CLONE_DIR        Working clone path
  VALUES_FILE      Helm values (auto: values-abeval-evalflow.yaml in guy-ziv-evalflow)
  SSH_KEY_PATH     Public key for --set sshPublicKey (optional)
EOF
}

SKIP_HELM=false
SKIP_CLONE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --skip-helm) SKIP_HELM=true; shift ;;
    --skip-clone) SKIP_CLONE=true; shift ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

need() { command -v "$1" >/dev/null 2>&1 || { echo "error: missing $1" >&2; exit 1; }; }
need oc
need helm
need git

if ! oc whoami >/dev/null 2>&1; then
  echo "error: oc is not logged in" >&2
  exit 1
fi

echo "=== forge-saw bootstrap (not evaluate) ==="
echo "  repo:     $FORGE_SAW_REPO"
echo "  pin:      $FORGE_SAW_PIN"
echo "  namespace:$SAW_NS"
echo "  eval ns:  $EVAL_NS"
echo "  release:  $RELEASE"
echo "  values:   $VALUES_FILE"

if ! oc get namespace "$SAW_NS" >/dev/null 2>&1; then
  echo "error: namespace $SAW_NS does not exist (this login cannot create projects)." >&2
  echo "       Create it as cluster-admin or set SAW_NS to a namespace you already have." >&2
  exit 1
fi
oc project "$SAW_NS" >/dev/null

if ! oc api-resources --api-group=kubevirt.io -o name 2>/dev/null | grep -qx 'virtualmachines.kubevirt.io'; then
  echo "error: VirtualMachine API is not available on this cluster." >&2
  echo "       forge-saw Helm creates a KubeVirt VM (not a Deployment)." >&2
  echo "       Install OpenShift Virtualization, or point evaluate at a gateway that already exists." >&2
  echo "       SAW_NS=$SAW_NS is usable for PipelineRuns and Secrets; it does not replace CNV." >&2
  exit 1
fi

if [[ "$SKIP_CLONE" != true ]]; then
  if [[ -d "$CLONE_DIR/.git" ]]; then
    echo "Updating clone at $CLONE_DIR"
    git -C "$CLONE_DIR" fetch --depth 1 origin "$FORGE_SAW_PIN"
    git -C "$CLONE_DIR" checkout --detach "$FORGE_SAW_PIN"
  else
    mkdir -p "$(dirname "$CLONE_DIR")"
    git clone --filter=blob:none "$FORGE_SAW_REPO" "$CLONE_DIR"
    git -C "$CLONE_DIR" checkout --detach "$FORGE_SAW_PIN"
  fi
fi

if [[ "$SKIP_HELM" != true ]]; then
  if [[ ! -d "$CLONE_DIR/charts/openshell-saw" ]]; then
    echo "error: $CLONE_DIR/charts/openshell-saw missing — clone pin first" >&2
    exit 1
  fi

  HELM_EXTRA=()
  if [[ -n "${SSH_KEY_PATH:-}" && -f "${SSH_KEY_PATH}.pub" ]]; then
    HELM_EXTRA+=(--set "sshPublicKey=$(cat "${SSH_KEY_PATH}.pub")")
  elif [[ -f "${HOME}/.generated-ssh-keys/sandbox-ssh.pub" ]]; then
    HELM_EXTRA+=(--set "sshPublicKey=$(cat "${HOME}/.generated-ssh-keys/sandbox-ssh.pub")")
  else
    echo "WARN: no SSH public key found. Run 'make generate-keys' in the forge-saw clone,"
    echo "      or set SSH_KEY_PATH. Helm may still install if the chart allows empty sshPublicKey."
  fi

  echo "=== helm upgrade --install $RELEASE ==="
  helm upgrade --install "$RELEASE" "$CLONE_DIR/charts/openshell-saw" \
    --namespace "$SAW_NS" \
    -f "$VALUES_FILE" \
    "${HELM_EXTRA[@]}"
fi

echo "=== wait for VirtualMachine $RELEASE ==="
if oc get vm "$RELEASE" -n "$SAW_NS" >/dev/null 2>&1; then
  oc wait vm/"$RELEASE" -n "$SAW_NS" --for=condition=Ready --timeout="$WAIT_TIMEOUT" \
    || oc wait vm/"$RELEASE" -n "$SAW_NS" --for=jsonpath='{.status.printableStatus}'=Running --timeout="$WAIT_TIMEOUT" \
    || echo "WARN: VM wait timed out; check: oc get vm,vmi,pod -n $SAW_NS"
else
  echo "WARN: VirtualMachine $RELEASE not found yet"
fi

if oc get job "${RELEASE}-setup" -n "$SAW_NS" >/dev/null 2>&1; then
  echo "=== wait for setup Job ${RELEASE}-setup ==="
  oc wait job/"${RELEASE}-setup" -n "$SAW_NS" --for=condition=complete --timeout="$WAIT_TIMEOUT" \
    || echo "WARN: setup Job not complete; check: oc logs job/${RELEASE}-setup -n $SAW_NS"
fi

echo "=== apply NetworkPolicy (ingress 17670 from $EVAL_NS) ==="
NP_TMP="$(mktemp)"
sed -e "s/namespace: .*/namespace: ${SAW_NS}/" \
    -e "s/kubernetes.io\/metadata.name: .*/kubernetes.io\/metadata.name: ${EVAL_NS}/" \
    "$NETWORKPOLICY_FILE" > "$NP_TMP"
oc apply -f "$NP_TMP"
rm -f "$NP_TMP"

echo
echo "Gateway Service (in-cluster URL for evaluate):"
echo "  https://${RELEASE}-gateway.${SAW_NS}.svc.cluster.local:17670"
echo
echo "Next (ops, not evaluate):"
echo "  1. Copy mTLS client files off the VM (see $SCRIPT_DIR/README.md)"
echo "  2. Create Secret openshell-mtls in $EVAL_NS (PipelineRun namespace)"
echo "  3. Create Secret openshell-credentials in $EVAL_NS (see $SCRIPT_DIR/create-openshell-credentials.sh)"
echo "  4. Trigger a PipelineRun only after that Secret has real M365_ACCESS_TOKEN + M365_USER"
echo
echo "This script lives at $SCRIPT_DIR relative to $REPO_ROOT."
