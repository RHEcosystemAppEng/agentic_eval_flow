#!/usr/bin/env bash
# Pull a run's archived step pod logs from MinIO into a local directory.
#
# The finally cleanup task archives every DAG pod's complete step logs to
# <report-prefix>/debug/pod-logs/<run>/ before releasing the pods (see
# pipeline/tasks/post/cleanup_pvc.yaml); with them gone from the cluster,
# this archive is the only complete per-step log record of the run.
#
# Usage:
#   scripts/fetch_run_logs.sh <pipelinerun-name> [namespace] [out-dir]
#   scripts/fetch_run_logs.sh scharan-verify-cleanup-112-7
#   scripts/fetch_run_logs.sh scharan-verify-cleanup-112-7 forge-nommen /tmp/logs
#
# All MinIO access runs inside the MinIO deployment's pod, which already
# has mc and the credentials — they are never printed or exported here.
set -euo pipefail

RUN="${1:?usage: $0 <pipelinerun-name> [namespace] [out-dir]}"
NS="${2:-forge-nommen}"
OUT="${3:-${RUN}-logs}"
BUCKET="${MINIO_BUCKET:-ab-eval-reports}"

command -v oc >/dev/null 2>&1 || { echo "oc is required" >&2; exit 1; }
command -v awk >/dev/null 2>&1 || { echo "awk is required" >&2; exit 1; }
mkdir -p "$OUT"

# All remote commands share this alias bootstrap.
mc() {
  oc -n "$NS" exec deploy/minio -- sh -c "
    mc alias set local http://localhost:9000 \"\$MINIO_ROOT_USER\" \"\$MINIO_ROOT_PASSWORD\" >/dev/null 2>&1 && mc $*
  "
}

# Resolve the run's report prefix: the store task names it
# <date>_<time>_<submission>_<run>; runs whose store never ran fall back
# to the root-level pod-logs/ path the cleanup task also uses.
PREFIX="$(mc "ls local/${BUCKET}/" 2>/dev/null \
  | grep -E "_${RUN}/$" | awk '{print $NF}' | tail -1 || true)"
BASE="local/${BUCKET}/${PREFIX}debug/pod-logs/${RUN}"

LISTING="$(mc "ls ${BASE}/" 2>/dev/null || true)"
if [ -z "$LISTING" ]; then
  echo "No archived pod logs found for ${RUN} at ${BASE#local/}" >&2
  exit 1
fi

echo "Pulling ${BASE#local/} into ${OUT}/"
count=0
while read -r obj; do
  [ -n "$obj" ] || continue
  name="${obj##*/}"
  mc "cat ${BASE}/${name}" > "${OUT}/${name}" 2>/dev/null
  if [ -s "${OUT}/${name}" ]; then
    echo "  ${name} ($(wc -c < "${OUT}/${name}") bytes)"
  else
    echo "  WARNING: ${name} came back empty (archive incomplete?)"
  fi
  count=$((count + 1))
done < <(echo "$LISTING" | awk '{print $NF}')

echo "Wrote ${count} log file(s) to ${OUT}/"
