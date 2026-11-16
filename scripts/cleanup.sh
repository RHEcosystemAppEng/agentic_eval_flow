#!/usr/bin/env bash
set -euo pipefail

# Cleanup script for ab-eval-flow namespace.
# Intended to run as a CronJob to remove stale resources.

NAMESPACE="${NAMESPACE:-ab-eval-flow}"
POD_AGE_HOURS="${POD_AGE_HOURS:-24}"
PIPELINERUN_KEEP_COUNT="${PIPELINERUN_KEEP_COUNT:-7}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

log "Starting cleanup in namespace=${NAMESPACE}"

# Delete completed/failed trial pods older than threshold
log "Removing completed/failed pods older than ${POD_AGE_HOURS}h..."
completed=$(oc get pods -n "${NAMESPACE}" \
  --field-selector=status.phase==Succeeded \
  -o jsonpath='{.items[?(@.metadata.labels.abevalflow/role=="trial")].metadata.name}' 2>/dev/null || true)
failed=$(oc get pods -n "${NAMESPACE}" \
  --field-selector=status.phase==Failed \
  -o jsonpath='{.items[?(@.metadata.labels.abevalflow/role=="trial")].metadata.name}' 2>/dev/null || true)

for pod in ${completed} ${failed}; do
  age_seconds=$(oc get pod "${pod}" -n "${NAMESPACE}" \
    -o jsonpath='{.status.startTime}' 2>/dev/null | \
    xargs -I{} python3 -c "
from datetime import datetime, timezone
start = datetime.fromisoformat('{}').replace(tzinfo=timezone.utc)
print(int((datetime.now(timezone.utc) - start).total_seconds()))
" 2>/dev/null || echo 0)
  threshold=$((POD_AGE_HOURS * 3600))
  if [ "${age_seconds}" -gt "${threshold}" ]; then
    log "Deleting pod ${pod} (age=${age_seconds}s)"
    oc delete pod "${pod}" -n "${NAMESPACE}" --grace-period=0 || true
  fi
done

# Delete old PipelineRuns (keep the N most recent by count)
log "Pruning PipelineRuns, keeping most recent ${PIPELINERUN_KEEP_COUNT}..."
# A retained evidence PVC is the durable archive gate. Never garbage collect
# its PipelineRun/TaskRuns (and therefore Pod logs) before recovery completes.
runs_file=$(mktemp)
claims_file=$(mktemp)
trap 'rm -f "$runs_file" "$claims_file"' EXIT
oc get pipelineruns -n "${NAMESPACE}" -o json > "$runs_file"
oc get pvc -n "${NAMESPACE}" -l abevalflow.io/mcp-run -o json > "$claims_file"
for run in $(python3 - "$runs_file" "$claims_file" "$PIPELINERUN_KEEP_COUNT" <<'PYTHON'
import json, sys
runs = json.load(open(sys.argv[1]))["items"]
claims = json.load(open(sys.argv[2]))["items"]
protected = {c["metadata"]["labels"]["abevalflow.io/mcp-run"] for c in claims}
completed = sorted((r for r in runs if r.get("status", {}).get("completionTime")),
                   key=lambda r: r["metadata"]["creationTimestamp"], reverse=True)
for run in completed[int(sys.argv[3]):]:
    if run["metadata"]["uid"] not in protected:
        print(run["metadata"]["name"])
PYTHON
); do
  oc delete pipelinerun "$run" -n "${NAMESPACE}"
done

# Prune internal registry images for deleted submissions
log "Pruning unused image streams..."
oc get imagestream -n "${NAMESPACE}" -o name 2>/dev/null | while read -r is; do
  tag_count=$(oc get "${is}" -n "${NAMESPACE}" -o jsonpath='{.status.tags}' 2>/dev/null | python3 -c "import sys,json; print(len(json.load(sys.stdin) or []))" 2>/dev/null || echo 0)
  if [ "${tag_count}" -eq 0 ]; then
    log "Deleting empty imagestream ${is}"
    oc delete "${is}" -n "${NAMESPACE}" || true
  fi
done

log "Cleanup complete"
