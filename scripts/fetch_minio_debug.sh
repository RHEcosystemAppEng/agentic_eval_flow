#!/usr/bin/env bash
# Fetch a Harbor/AEH debug tarball from MinIO via an in-cluster mc pod.
# Credentials come from the minio-credentials Secret (never printed).
#
# Usage:
#   scripts/fetch_minio_debug.sh <object-or-prefix> [local-out.tar.gz]
#   scripts/fetch_minio_debug.sh 20260719_084529_aeh-hello-world-single_aeh-verifier-fix7-nnksg
#   scripts/fetch_minio_debug.sh ab-eval-reports/.../debug/harbor/2026-07-19__08-44-52.tar.gz /tmp/out.tar.gz
set -euo pipefail

NS="${NAMESPACE:-guy-ziv-evalflow}"
BUCKET="${MINIO_BUCKET:-ab-eval-reports}"
POD="aeh-minio-fetch"
TARGET="${1:?usage: $0 <prefix-or-object-path> [local-out.tar.gz]}"
OUT="${2:-/tmp/aeh-minio-local/$(basename "$TARGET" | sed 's|/$||').tar.gz}"

if [[ "$TARGET" != *.tar.gz ]]; then
  # Treat as report prefix; resolve newest harbor debug tarball under it
  PREFIX="$TARGET"
  PREFIX="${PREFIX#ab-eval-reports/}"
  PREFIX="${PREFIX%/}"
  RESOLVE=1
else
  OBJ="$TARGET"
  OBJ="${OBJ#ab-eval-reports/}"
  RESOLVE=0
fi

mkdir -p "$(dirname "$OUT")"

oc delete pod "$POD" -n "$NS" --ignore-not-found >/dev/null
oc apply -n "$NS" -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${POD}
spec:
  restartPolicy: Never
  containers:
  - name: mc
    image: quay.io/minio/mc:latest
    command: ["sleep", "600"]
    env:
    - name: HOME
      value: /tmp
    - name: MINIO_ENDPOINT
      valueFrom:
        secretKeyRef:
          name: minio-credentials
          key: endpoint-url
    - name: MINIO_ACCESS_KEY
      valueFrom:
        secretKeyRef:
          name: minio-credentials
          key: root-user
    - name: MINIO_SECRET_KEY
      valueFrom:
        secretKeyRef:
          name: minio-credentials
          key: root-password
    securityContext:
      allowPrivilegeEscalation: false
      capabilities:
        drop: ["ALL"]
      seccompProfile:
        type: RuntimeDefault
  securityContext:
    runAsNonRoot: true
    seccompProfile:
      type: RuntimeDefault
EOF

for _ in $(seq 1 60); do
  phase=$(oc get pod "$POD" -n "$NS" -o jsonpath='{.status.phase}' 2>/dev/null || echo Pending)
  [[ "$phase" == "Running" ]] && break
  sleep 2
done
phase=$(oc get pod "$POD" -n "$NS" -o jsonpath='{.status.phase}')
[[ "$phase" == "Running" ]] || { echo "pod not Running: $phase" >&2; exit 1; }

oc exec -n "$NS" "$POD" -- sh -c '
  set -e
  export HOME=/tmp
  EP="${MINIO_ENDPOINT#http://}"; EP="${EP#https://}"
  mc alias set local "http://$EP" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY" >/dev/null
'

if [[ "$RESOLVE" -eq 1 ]]; then
  OBJ=$(oc exec -n "$NS" "$POD" -- sh -c "
    export HOME=/tmp
    mc find local/${BUCKET}/${PREFIX} --name '*.tar.gz' 2>/dev/null | sort | tail -1
  ")
  OBJ="${OBJ#local/}"
  [[ -n "$OBJ" ]] || { echo "No .tar.gz under s3://${BUCKET}/${PREFIX}" >&2; exit 1; }
  echo "Resolved object: s3://${OBJ}"
fi

oc exec -n "$NS" "$POD" -- sh -c "
  set -e
  export HOME=/tmp
  mc cp 'local/${OBJ}' /tmp/harbor-job.tar.gz >/dev/null
  ls -la /tmp/harbor-job.tar.gz
"
# mc image has no tar; stream bytes out
oc exec -n "$NS" "$POD" -- cat /tmp/harbor-job.tar.gz > "$OUT"
echo "Wrote $OUT ($(wc -c < "$OUT") bytes)"
