#!/usr/bin/env bash
# Shared gate wrapper for static/conformance evaluation, baked into the MCP eval image.
#
# All evaluation gate steps do the same thing: run the evaluation's gate module, emit
# the Tekton results (passed flag + report path) from the summary JSON, then exit
# with the gate's own code so a failing gate fails the step. Keeping it here means
# the tasks stay consistent when this behavior changes.
#
# Usage:
#   gate_wrapper.sh <gate-module> <reports-dir> <mode> <summary-json> \
#               <passed-result-path> <report-rel> <report-path-result>
set -uo pipefail

GATE_MODULE="$1"        # e.g. scripts.mcp.runners.static_eval_gate
RESULTS_DIR="$2"        # absolute results dir for this submission/evaluation (input scans)
REPORTS_DIR="$3"        # absolute reports dir for this submission/evaluation (output summaries)
MODE="$4"               # block | warn | disabled
SUMMARY="$5"            # *-eval-summary.json written by the gate module
PASSED_RESULT_PATH="$6" # Tekton result path for the passed flag
REPORT_REL="$7"         # workspace-relative report path (result value)
REPORT_PATH_RESULT="$8" # Tekton result path for the report path

# Never abort before writing results: capture the gate's exit code, emit the
# Tekton results, then propagate the code so the step fails if a gate failed.
python -m "$GATE_MODULE" --results-dir "$RESULTS_DIR" --reports-dir "$REPORTS_DIR" --mode "$MODE"
GATE_EXIT=$?

# Emit the passed flag from the summary. A missing summary or one without a
# 'passed' key is itself a failure: fail loudly rather than writing an empty
# result that a downstream `when` would read as neither true nor false.
if ! python3 -c "import json,sys;print(str(json.load(open(sys.argv[1]))['passed']).lower(),end='')" \
    "$SUMMARY" > "$PASSED_RESULT_PATH"; then
  echo "run_gate: gate summary $SUMMARY is missing or has no 'passed' key" >&2
  exit 1
fi
printf '%s' "$REPORT_REL" > "$REPORT_PATH_RESULT"

exit "$GATE_EXIT"
