#!/usr/bin/env bash
# Stage-by-stage results for a PipelineRun after its cleanup ran: the
# finally cleanup task releases the run's pods to reclaim the workspace
# PVC, and the pod logs go with them — but every TaskRun object keeps
# its status, step conditions, timings and results. This helper
# aggregates those, so a cleaned-up run stays reviewable.
#
# Usage:
#   scripts/pipelinerun-report.sh <pipelinerun-name> [namespace]
#   scripts/pipelinerun-report.sh <pipelinerun-name> [namespace] --steps <pipeline-task>
#   scripts/pipelinerun-report.sh scharan-verify-cleanup-112-2 forge-nommen
#   scripts/pipelinerun-report.sh scharan-verify-cleanup-112-2 forge-nommen --steps evaluate
#
# Requires oc and jq. Full reports (scores, scorecard, artifacts) live in
# MLflow/MinIO; this covers statuses, timings and task results.
set -euo pipefail

command -v oc >/dev/null || { echo "ERROR: oc is required" >&2; exit 1; }
command -v jq >/dev/null || { echo "ERROR: jq is required" >&2; exit 1; }

usage() {
  echo "usage: $0 <pipelinerun-name> [namespace] [--steps <pipeline-task>]" >&2
  exit 2
}

RUN=""
NS=""
STEPS_TASK=""
while [ $# -gt 0 ]; do
  case "$1" in
    --steps) [ $# -ge 2 ] || usage; STEPS_TASK="$2"; shift 2 ;;
    -h|--help) usage ;;
    *)
      if [ -z "$RUN" ]; then RUN="$1"
      elif [ -z "$NS" ]; then NS="$1"
      else usage; fi
      shift ;;
  esac
done
[ -n "$RUN" ] || usage

# oc with an optional namespace (a wrapper instead of an array, so empty
# expansion cannot trip set -u on old bash).
koc() {
  if [ -n "$NS" ]; then oc -n "$NS" "$@"; else oc "$@"; fi
}

if ! koc get pipelinerun "$RUN" >/dev/null 2>&1; then
  echo "ERROR: PipelineRun $RUN not found${NS:+ in namespace $NS}" >&2
  exit 1
fi

if [ -n "$STEPS_TASK" ]; then
  # Drill into one stage: every container step with its termination
  # reason and exit code (the latest TaskRun, if the task was retried).
  koc get taskrun -l "tekton.dev/pipelineRun=$RUN,tekton.dev/pipelineTask=$STEPS_TASK" -o json \
    | jq -r --arg task "$STEPS_TASK" '
      .items | sort_by(.status.startTime) | last
      | (if . == null or (.status.steps // []) == []
         then [{name: "(no step status recorded for \($task))"}]
         else .status.steps end)[]
      | [.name, (.terminated.reason // "-"), ("exit=\(.terminated.exitCode // "-")")]
      | @tsv' \
    | column -t -s $'\t'
  exit 0
fi

koc get taskrun -l "tekton.dev/pipelineRun=$RUN" -o json \
  | jq -r '
    def dur: ((.status.completionTime | fromdateiso8601) - (.status.startTime | fromdateiso8601));
    .items | sort_by(.status.startTime)[] |
    [ .metadata.labels["tekton.dev/pipelineTask"] // .metadata.name,
      .status.conditions[0].reason // "?",
      (if .status.completionTime then "\(dur)s" else "-" end),
      ((.status.results // []) | map("\(.name)=\(.value | tostring | gsub("\\s+"; " "))") | join("  "))
    ] | @tsv' \
  | column -t -s $'\t'
