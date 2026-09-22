#!/usr/bin/env bash
# Create Secret openshell-credentials in the PipelineRun namespace from the
# caller's environment. Does not print secret values. Refuses placeholders.
set -euo pipefail

NS="${EVAL_NS:-guy-ziv-evalflow}"
SECRET_NAME="${OPENSHELL_CREDENTIALS_SECRET:-openshell-credentials}"

placeholder() {
  local v="${1:-}"
  local low
  low="$(printf '%s' "$v" | tr '[:upper:]' '[:lower:]')"
  [[ -z "${v// }" ]] && return 0
  [[ "$low" == *"<replace-with"* || "$low" == *changeme* || "$low" == *placeholder* ]]
}

require() {
  local key="$1"
  local val="${!key:-}"
  if placeholder "$val"; then
    echo "ERROR: $key is unset or still a placeholder. Export a real value, then rerun." >&2
    echo "Do not oc apply config/forge-saw/secret-openshell-credentials.yaml while it contains <replace-with-…>." >&2
    exit 1
  fi
}

require M365_ACCESS_TOKEN
require M365_USER

ARGS=(
  --from-literal="M365_ACCESS_TOKEN=${M365_ACCESS_TOKEN}"
  --from-literal="M365_USER=${M365_USER}"
)

for key in M365_TENANT_ID M365_CLIENT_ID M365_CLIENT_SECRET; do
  val="${!key:-}"
  if ! placeholder "$val"; then
    ARGS+=(--from-literal="${key}=${val}")
  fi
done

echo "Creating Secret ${SECRET_NAME} in ${NS} (values not printed)."
echo "Keys: M365_ACCESS_TOKEN, M365_USER, and any optional M365_TENANT_ID/CLIENT_* you exported."
echo "M365_AUTH_HEADER_FILE and M365_GRAPH_CURL are not Secret keys — AEH writes them in the sandbox."

oc create secret generic "$SECRET_NAME" -n "$NS" "${ARGS[@]}" \
  --dry-run=client -o yaml | oc apply -f -

echo "Verify keys (no values):"
oc get secret "$SECRET_NAME" -n "$NS" -o json | python3 -c 'import json,sys; d=json.load(sys.stdin); print("\n".join(sorted((d.get("data") or {}))))'
echo "Done. Re-run the OpenShell PipelineRun after this Secret exists."
