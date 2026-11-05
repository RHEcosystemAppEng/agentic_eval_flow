#!/usr/bin/env bash
#
# run_mcp_eval.sh - Run the MCP-server evaluation Static eval + Conformance eval against any
# MCP server you point it at. This is a demo/dev driver (uncommitted): it mirrors
# what the two Tekton Tasks do (pipeline/tasks/konflux/mcp-static-eval.yaml and
# mcp-conformance-eval.yaml), so a local run and a cluster run exercise the same scanners,
# probe, and gates.
#
# Two execution modes:
#
#   local (default)  Run everything on this machine, no cluster needed.
#                    Static eval: run the three static scanners (gitleaks / semgrep /
#                    licensee) over a repo, then the Static eval gate.
#                    Conformance eval: probe a running MCP server over Streamable HTTP,
#                    optionally consume a Compass facts file, then the Conformance eval gate.
#
#   --oc             Run on OpenShift/Konflux instead, by starting the real
#                    Tekton Tasks (mcp-static-eval, mcp-conformance-eval).
#                    This is the "pipeline demo, two phases only" path. It needs a
#                    cluster you are logged in to (oc), tkn, the Tasks applied, and
#                    a workspace PVC. Without those it prints a preflight report and
#                    the exact commands it would run, then exits (nothing is
#                    executed on a cluster you cannot reach).
#
# Scope: Static eval (static) + Conformance eval (deterministic conformance) only, per the ADR
# (Approach 4). Behavioral evaluation and pipeline wiring are out of scope here.
#
# Examples:
#   # Local, both phases, against a repo dir and a running mock server:
#   bash scripts/mcp/run_mcp_eval.sh \
#       --repo /tmp/generic-mock-mcp-server \
#       --server-url http://localhost:8080/mcp
#
#   # Local, Static eval only (no server given):
#   bash scripts/mcp/run_mcp_eval.sh --repo https://github.com/some/mcp-server.git
#
#   # Local, Conformance eval only (no repo given), with Compass facts:
#   bash scripts/mcp/run_mcp_eval.sh --server-url http://localhost:8080/mcp \
#       --compass-facts /tmp/facts.json
#
#   # Cluster (Tekton) - later, when you have access. Prints the plan today:
#   bash scripts/mcp/run_mcp_eval.sh --oc --pvc mcp-eval-source \
#       --repo https://github.com/some/mcp-server.git \
#       --server-url http://my-mcp:8080/mcp
#
# Standing constraints for this file: it never commits, pushes, or `git add`s
# anything, never writes to an external service in local mode, and never emits real
# secrets. The Compass facts it can consume are whatever file you pass; it does not
# fabricate one.

set -uo pipefail

# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

REPO=""            # Static eval target: local dir or git URL (empty => skip Static eval)
SUBDIR="."         # source subdir within the repo to scan
SERVER_URL=""      # Conformance eval target: live MCP endpoint (empty => skip live probe)
COMPASS_FACTS=""   # optional Compass facts JSON for the consumed Conformance eval checks
MODE="block"       # gate mode: block | warn | disabled
TIMEOUT="30"       # Conformance eval per-request HTTP timeout (seconds)
OPENAPI_URL=""     # explicit OpenAPI contract URL (empty => probe well-known paths)
ALLOW=""           # space-separated SPDX allow-list override for the license gate
SUBMISSION=""      # report dir name (defaults to the repo basename or "local")
REPORTS_ROOT=""    # where per-phase reports go (defaults to a temp dir)

RUN_OC=0           # 1 => run on cluster via Tekton instead of locally
DRY_RUN=0          # --oc only: print the tkn commands, do not execute
NAMESPACE="ab-eval-flow"   # matches the Task metadata.namespace
PVC=""             # cluster workspace PVC claimName (required for --oc)

# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
c_bold=$'\033[1m'; c_red=$'\033[31m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_off=$'\033[0m'
log()  { printf '%s\n' "$*"; }
info() { printf '%s\n' "${c_bold}$*${c_off}"; }
warn() { printf '%s\n' "${c_ylw}WARNING: $*${c_off}" >&2; }
err()  { printf '%s\n' "${c_red}ERROR: $*${c_off}" >&2; }
die()  { err "$*"; exit 1; }

usage() { sed -n '2,60p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
while [ $# -gt 0 ]; do
  case "$1" in
    --repo)            REPO="$2"; shift 2 ;;
    --subdir)          SUBDIR="$2"; shift 2 ;;
    --server-url)      SERVER_URL="$2"; shift 2 ;;
    --compass-facts)   COMPASS_FACTS="$2"; shift 2 ;;
    --mode)            MODE="$2"; shift 2 ;;
    --timeout)         TIMEOUT="$2"; shift 2 ;;
    --openapi-url)     OPENAPI_URL="$2"; shift 2 ;;
    --allow)           ALLOW="${ALLOW:+$ALLOW }$2"; shift 2 ;;
    --submission-name) SUBMISSION="$2"; shift 2 ;;
    --reports-dir)     REPORTS_ROOT="$2"; shift 2 ;;
    --oc)              RUN_OC=1; shift ;;
    --dry-run)         DRY_RUN=1; shift ;;
    --namespace|-n)    NAMESPACE="$2"; shift 2 ;;
    --pvc)             PVC="$2"; shift 2 ;;
    -h|--help)         usage; exit 0 ;;
    *) die "Unknown argument: $1 (use --help)" ;;
  esac
done

case "$MODE" in block|warn|disabled) ;; *) die "--mode must be block|warn|disabled (got '$MODE')" ;; esac
[ -n "$REPO" ] || [ -n "$SERVER_URL" ] || die "Give at least one of --repo (Static eval) or --server-url (Conformance eval). See --help."

# Report directory name mirrors the Tekton reports/<submission-name>/ layout.
if [ -z "$SUBMISSION" ]; then
  if [ -n "$REPO" ]; then SUBMISSION="$(basename "${REPO%.git}")"; else SUBMISSION="local"; fi
fi

# ==========================================================================  #
# CLUSTER MODE (--oc): start the real Tekton Tasks.
# ==========================================================================  #
if [ "$RUN_OC" -eq 1 ]; then
  info "=== MCP eval on OpenShift/Konflux via Tekton (submission: $SUBMISSION, mode: $MODE) ==="
  log "This runs the same two Tasks the pipeline would: mcp-static-eval + mcp-conformance-eval."
  log ""

  # Build the exact commands we would run, so they are visible whether or not the
  # cluster is reachable. tkn reads the params/workspaces defined in the Task YAML.
  p1_cmd=(tkn task start mcp-static-eval -n "$NAMESPACE"
          -p submission-name="$SUBMISSION" -p mode="$MODE" -p source-subdir="$SUBDIR")
  [ -n "$ALLOW" ] && p1_cmd+=(-p allowed-licenses="$ALLOW")
  p1_cmd+=(-w "name=source,claimName=$PVC" --showlog)

  p2_cmd=(tkn task start mcp-conformance-eval -n "$NAMESPACE"
          -p server-url="$SERVER_URL" -p submission-name="$SUBMISSION" -p mode="$MODE" -p timeout="$TIMEOUT")
  [ -n "$COMPASS_FACTS" ] && p2_cmd+=(-p compass-facts-file="$COMPASS_FACTS")
  p2_cmd+=(-w "name=source,claimName=$PVC" --showlog)

  # Print a command on one indented line, single-quoting only args that need it
  # (e.g. a multi-word allowed-licenses), so the line is copy-paste runnable.
  print_cmd() {
    local out="  " a
    for a in "$@"; do
      case "$a" in
        *[!A-Za-z0-9=:/._,-]*) out+="'${a//\'/\'\\\'\'}' " ;;
        *) out+="$a " ;;
      esac
    done
    printf '%s\n' "${out% }"
  }
  print_plan() {
    log "${c_bold}Static eval (static):${c_off}"; print_cmd "${p1_cmd[@]}"; log ""
    if [ -n "$SERVER_URL" ]; then
      log "${c_bold}Conformance eval (conformance):${c_off}"; print_cmd "${p2_cmd[@]}"
    else
      log "${c_bold}Conformance eval:${c_off} skipped (no --server-url)."
    fi
  }

  # Preflight: everything the cluster path needs. Missing anything => print the
  # plan and what to fix, then exit without touching a cluster.
  problems=()
  command -v oc  >/dev/null 2>&1 || problems+=("oc CLI not found on PATH")
  command -v tkn >/dev/null 2>&1 || problems+=("tkn (Tekton) CLI not found on PATH")
  [ -n "$PVC" ] || problems+=("--pvc <claimName> is required (the 'source' workspace PVC)")
  if command -v oc >/dev/null 2>&1; then
    oc whoami >/dev/null 2>&1 || problems+=("not logged in to a cluster (run: oc login ...)")
    if oc whoami >/dev/null 2>&1; then
      oc get task mcp-static-eval      -n "$NAMESPACE" >/dev/null 2>&1 || problems+=("Task mcp-static-eval not found in namespace $NAMESPACE (apply pipeline/tasks/konflux/mcp-static-eval.yaml)")
      oc get task mcp-conformance-eval -n "$NAMESPACE" >/dev/null 2>&1 || problems+=("Task mcp-conformance-eval not found in namespace $NAMESPACE (apply pipeline/tasks/konflux/mcp-conformance-eval.yaml)")
      [ -n "$PVC" ] && { oc get pvc "$PVC" -n "$NAMESPACE" >/dev/null 2>&1 || problems+=("PVC '$PVC' not found in namespace $NAMESPACE"); }
    fi
  fi

  log "Plan:"; log ""; print_plan; log ""
  warn "The 'source' workspace PVC must already contain the MCP server source at --subdir '$SUBDIR'."
  warn "This script does not populate the PVC; populate it first with a git-clone Task or 'oc cp'."
  log ""

  if [ "${#problems[@]}" -gt 0 ]; then
    err "Cannot run on a cluster from here yet:"
    for p in "${problems[@]}"; do log "  - $p"; done
    log ""
    log "Fix the above (or run this later when you have cluster access). The plan printed above is what will run."
    exit 2
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    info "Preflight passed. --dry-run set: not executing. Commands above are ready to run."
    exit 0
  fi

  # Cluster is reachable and everything is in place: run for real.
  info "Preflight passed. Starting Static eval Task..."
  "${p1_cmd[@]}"; p1_rc=$?
  if [ -n "$SERVER_URL" ]; then
    info "Starting Conformance eval Task..."
    "${p2_cmd[@]}"; p2_rc=$?
  else
    p2_rc=0; warn "Conformance eval skipped (no --server-url)."
  fi
  log ""
  info "=== Cluster run submitted. Inspect TaskRuns with: tkn taskrun list -n $NAMESPACE ==="
  [ "$p1_rc" -eq 0 ] && [ "$p2_rc" -eq 0 ] && exit 0 || exit 1
fi

# ==========================================================================  #
# LOCAL MODE (default): run scanners/probe/gates on this machine.
# ==========================================================================  #

# Prefer `uv run` so semgrep (installed in the project venv) resolves for the
# scanner subprocess; fall back to plain python3 on PYTHONPATH otherwise.
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if command -v uv >/dev/null 2>&1 && [ -f "$ROOT/pyproject.toml" ]; then
  PY=(uv run python)
else
  PY=(python3)
fi
pyrun() { ( cd "$ROOT" && "${PY[@]}" "$@" ); }

if [ -z "$REPORTS_ROOT" ]; then
  REPORTS_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/mcp-eval-XXXXXX")"
fi
RESULTS_DIR="$REPORTS_ROOT/results"
REPORTS_DIR="$REPORTS_ROOT/reports"
mkdir -p "$RESULTS_DIR" "$REPORTS_DIR"

info "=== MCP eval (local) - submission: $SUBMISSION, mode: $MODE ==="
log "Reports: $REPORTS_ROOT"
log ""

# --- Resolve the Static eval target: clone if it looks like a git URL. -----------
CLONE_DIR=""
cleanup() { [ -n "$CLONE_DIR" ] && rm -rf "$CLONE_DIR"; }
trap cleanup EXIT

TARGET=""
if [ -n "$REPO" ]; then
  case "$REPO" in
    http://*|https://*|git@*|ssh://*)
      CLONE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/mcp-src-XXXXXX")"
      info "Cloning Static eval target: $REPO"
      if ! git clone --depth 1 "$REPO" "$CLONE_DIR" 2>&1 | tail -2; then
        die "git clone failed for $REPO"
      fi
      TARGET="$CLONE_DIR/$SUBDIR"
      ;;
    *)
      [ -d "$REPO" ] || die "--repo is neither a git URL nor an existing directory: $REPO"
      TARGET="$REPO/$SUBDIR"
      ;;
  esac
  [ -d "$TARGET" ] || die "source subdir does not exist: $TARGET"
fi

# =============================== PHASE 1 =================================== #
P1_RAN=0; P1_PASSED=""
if [ -n "$TARGET" ]; then
  P1_RAN=1
  info "--- Static eval (static) on $TARGET ---"

  have_gitleaks=0; have_semgrep=0; have_licensee=0
  command -v gitleaks >/dev/null 2>&1 && have_gitleaks=1 || warn "gitleaks not found; the secrets scan will be skipped (its gate then depends on --mode: block => FAIL on the missing scan)."
  # semgrep resolves under `uv run`; check both directly and via the runner.
  if command -v semgrep >/dev/null 2>&1 || ( cd "$ROOT" && "${PY[@]}" -c "import shutil,sys; sys.exit(0 if shutil.which('semgrep') else 1)" 2>/dev/null ); then
    have_semgrep=1
  else
    warn "semgrep not found; the no-user-code scan will be skipped (gate depends on --mode)."
  fi
  command -v licensee >/dev/null 2>&1 && have_licensee=1 || warn "licensee not found; falling back to a heuristic license check (labeled 'licensee(absent-stub)'). Install for a real result: gem install licensee."

  # secrets (gitleaks)
  if [ "$have_gitleaks" -eq 1 ]; then
    pyrun -m scripts.mcp.scanners.static_eval_secrets_scan "$TARGET" --reports-dir "$RESULTS_DIR" 2>&1 | tail -1
  fi
  # no-user-code (semgrep)
  if [ "$have_semgrep" -eq 1 ]; then
    pyrun -m scripts.mcp.scanners.static_eval_no_user_code_scan "$TARGET" --reports-dir "$RESULTS_DIR" 2>&1 | tail -1
  fi
  # license (licensee, or heuristic fallback that reuses the real evaluate_licenses)
  if [ "$have_licensee" -eq 1 ]; then
    # shellcheck disable=SC2086
    lic_allow=""; for s in $ALLOW; do lic_allow="$lic_allow --allow $s"; done
    # shellcheck disable=SC2086
    pyrun -m scripts.mcp.scanners.static_eval_license_scan "$TARGET" --reports-dir "$RESULTS_DIR" $lic_allow 2>&1 | tail -1
  else
    pyrun - "$RESULTS_DIR" "$TARGET" "$ALLOW" <<'PY'
# Heuristic license fallback for when the licensee binary is absent. Uses the REAL
# evaluate_licenses() + write_scan() so gate behavior matches production; only the
# licensee subprocess is replaced by a conservative marker scan of the license
# file. It never over-passes: an unrecognized or missing license => a finding.
import sys, re
from pathlib import Path
from scripts.mcp.scanners import static_eval_license_scan as license_scan
from scripts.mcp.scanners.helpers import write_scan
from abevalflow.mcp.gates.static_eval_license_gate import DEFAULT_ALLOWED_LICENSES

reports, target = Path(sys.argv[1]), Path(sys.argv[2])
allow_arg = sys.argv[3].split() if len(sys.argv) > 3 and sys.argv[3].strip() else []
allowed = {s.lower() for s in (allow_arg or DEFAULT_ALLOWED_LICENSES)}

# Unambiguous SPDX markers only; anything else stays UNKNOWN (which fails the gate).
MARKERS = [
    ("Apache-2.0", r"Apache License\s*,?\s*Version\s*2\.0"),
    ("MIT", r"\bMIT License\b|Permission is hereby granted, free of charge"),
    ("BSD-3-Clause", r"Redistribution and use.*Neither the name"),
    ("BSD-2-Clause", r"Redistribution and use"),
    ("GPL-3.0", r"GNU GENERAL PUBLIC LICENSE\s*Version 3"),
    ("GPL-2.0", r"GNU GENERAL PUBLIC LICENSE\s*Version 2"),
    ("MPL-2.0", r"Mozilla Public License Version 2\.0"),
    ("ISC", r"\bISC License\b"),
]
detected, matched = [], []
for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING", "COPYING.txt"):
    f = target / name
    if f.is_file():
        text = f.read_text(errors="ignore")
        spdx = next((s for s, pat in MARKERS if re.search(pat, text, re.I | re.S)), "UNKNOWN")
        detected = [{"spdx_id": spdx}]; matched = [{"filename": name}]
        break

findings = license_scan.evaluate_licenses(detected, allowed, matched)
write_scan(reports / "license-scan.json", "licensee(absent-stub)", target, findings)
lic = detected[0]["spdx_id"] if detected else "none"
print(f"INFO: license (heuristic, licensee absent): detected={lic}, {len(findings)} finding(s)")
PY
  fi

  # Static eval gate
  log ""
  pyrun -m scripts.mcp.runners.static_eval_gate --results-dir "$RESULTS_DIR" --reports-dir "$REPORTS_DIR" --mode "$MODE"
  P1_GATE_RC=$?
  P1_PASSED="$(pyrun -c "import json,sys;print('true' if json.load(open(sys.argv[1]))['passed'] else 'false')" "$REPORTS_DIR/static-eval-summary.json" 2>/dev/null)"
  # Static eval grade = mean of the three gate scores. All three always run, so this
  # is a real 0.00-1.00 grade, not just a pass/fail bit (ADR "Check Results").
  P1_GRADE="$(pyrun -c "import json,sys;g=json.load(open(sys.argv[1]))['checks'];s=[x['score'] for x in g];print(f'{sum(s)/len(s):.2f}')" "$REPORTS_DIR/static-eval-summary.json" 2>/dev/null)"
  pyrun - "$REPORTS_DIR/static-eval-summary.json" <<'PY' 2>/dev/null || true
import json, sys
s = json.load(open(sys.argv[1]))
print("  Static eval overall:", "PASS" if s["passed"] else "FAIL")
for g in s["checks"]:
    print(f"    {g['check']:<20} passed={str(g['passed']):<5} score={g['score']:.2f} findings={g['findings']}")
scores = [g["score"] for g in s["checks"]]
print(f"    grade: {sum(scores)/len(scores):.2f}  [mean of {len(scores)} gate scores]")
PY
else
  warn "No --repo given; skipping Static eval."
fi

# =============================== PHASE 2 =================================== #
log ""
P2_PASSED=""
info "--- Conformance eval (deterministic conformance) ---"
if [ -n "$SERVER_URL" ]; then
  info "Live probe against $SERVER_URL (timeout ${TIMEOUT}s)"
  pyrun -m scripts.mcp.probes.conformance_eval_probe --server-url "$SERVER_URL" --reports-dir "$RESULTS_DIR" --timeout "$TIMEOUT" --openapi-url "$OPENAPI_URL" 2>&1 | tail -3
else
  warn "No --server-url given; skipping the live probe. Probed checks will report not_evaluated."
fi
if [ -n "$COMPASS_FACTS" ]; then
  [ -f "$COMPASS_FACTS" ] || die "--compass-facts file not found: $COMPASS_FACTS"
  info "Compass consume from $COMPASS_FACTS"
  pyrun -m scripts.mcp.probes.conformance_eval_compass_probe --facts-file "$COMPASS_FACTS" --reports-dir "$RESULTS_DIR" 2>&1 | tail -1
else
  log "No --compass-facts given; Compass-consumed checks will report not_evaluated."
fi

log ""
pyrun -m scripts.mcp.runners.conformance_eval_gate --results-dir "$RESULTS_DIR" --reports-dir "$REPORTS_DIR" --mode "$MODE"
P2_GATE_RC=$?
P2_PASSED="$(pyrun -c "import json,sys;print('true' if json.load(open(sys.argv[1]))['passed'] else 'false')" "$REPORTS_DIR/conformance-eval-summary.json" 2>/dev/null)"
# Conformance eval grade = mean score over EVALUATED checks only (status pass/fail).
# not_evaluated checks carry score 1.0 and must be excluded, or an unprobed server
# would look perfect. "n/a" when nothing was evaluated (honest, not a free 1.00).
P2_GRADE="$(pyrun -c "import json,sys;ch=json.load(open(sys.argv[1]))['checks'];e=[x['score'] for x in ch if x['status'] in ('pass','fail')];print(f'{sum(e)/len(e):.2f}' if e else 'n/a')" "$REPORTS_DIR/conformance-eval-summary.json" 2>/dev/null)"
pyrun - "$REPORTS_DIR/conformance-eval-summary.json" <<'PY' 2>/dev/null || true
import json, sys
s = json.load(open(sys.argv[1]))
c = s["counts"]
print("  Conformance eval overall:", "PASS" if s["passed"] else "FAIL",
      f"(pass={c.get('pass',0)} fail={c.get('fail',0)} not_evaluated={c.get('not_evaluated',0)})")
for ch in s["checks"]:
    print(f"    {ch['check']:<22} {ch['status']:<14} score={ch['score']:.2f} findings={ch['findings']} [{ch['source']}]")
evald = [ch["score"] for ch in s["checks"] if ch["status"] in ("pass", "fail")]
grade = f"{sum(evald)/len(evald):.2f}" if evald else "n/a (all not_evaluated)"
print(f"    grade: {grade}  [{len(evald)} of {len(s['checks'])} checks evaluated; not_evaluated excluded]")
PY

# =============================== VERDICT =================================== #
log ""
info "=== Combined verdict (mode: $MODE) ==="
[ "$P1_RAN" -eq 1 ] && log "  Static eval: ${P1_PASSED:-unknown}  grade=${P1_GRADE:-?}  [mean of 3 gate scores]" || log "  Static eval: skipped"
log "  Conformance eval: ${P2_PASSED:-unknown}  grade=${P2_GRADE:-?}  [mean over evaluated checks]"
log "  Reports: $REPORTS_ROOT"

# Exit non-zero only when a gate actually failed (block mode). warn/disabled never
# block, mirroring the Tekton gate runners.
rc=0
[ "$P1_RAN" -eq 1 ] && [ "${P1_GATE_RC:-0}" -ne 0 ] && rc=1
[ "${P2_GATE_RC:-0}" -ne 0 ] && rc=1
exit "$rc"
