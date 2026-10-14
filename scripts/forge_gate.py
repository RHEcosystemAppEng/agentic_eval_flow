#!/usr/bin/env python3
"""Final gate runs after durable storage, including for a failed skill candidate."""

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path


def report_gate(reports, run_id):
    paths = list(reports.glob("*/" + run_id + "/cases/*/run_result.json"))
    if not paths:
        raise ValueError("no case results to gate")
    failures = []
    aggregates = [json.loads(p.read_text()) for p in reports.glob("*/report.json")]
    aggregate = next((r for r in aggregates if r.get("provenance", {}).get("pipeline_run_id") == run_id), None)
    if aggregate is None or aggregate.get("summary", {}).get("recommendation") != "pass":
        failures.append("aggregate quality gate did not pass")
    for path in paths:
        result = json.loads(path.read_text())
        if result.get("exit_code") != 0 or result.get("evaluation_status") != "passed":
            failures.append(path.parent.name + ": " + result.get("evaluation_status", "infrastructure_failed"))
        if result.get("usage", {}).get("complete") is not True:
            failures.append(path.parent.name + ": incomplete usage")
    for run in {path.parent.parent.parent for path in paths}:
        try:
            storage = json.loads((run / "storage-attestation.json").read_text())
            if storage.get("run") != run_id or storage.get("status") != "verified" or not storage.get("objects"):
                failures.append("invalid durable storage attestation")
        except (OSError, ValueError):
            failures.append("missing durable storage attestation")
    return failures


def verify_mlflow(uri, run_id):
    def get(path):
        with urllib.request.urlopen(uri.rstrip("/") + "/api/2.0/mlflow/" + path, timeout=20) as response:
            return json.load(response)

    experiment = get("experiments/get-by-name?" + urllib.parse.urlencode({"experiment_name": run_id}))["experiment"][
        "experiment_id"
    ]
    request = urllib.request.Request(
        uri.rstrip("/") + "/api/2.0/mlflow/runs/search",
        data=json.dumps({"experiment_ids": [experiment], "max_results": 100}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        runs = json.load(response).get("runs", [])
    completed = [r for r in runs if r["info"]["status"] == "FINISHED"]
    if len(completed) != 1:
        raise ValueError("expected one FINISHED MLflow run")
    files = get("artifacts/list?" + urllib.parse.urlencode({"run_id": completed[0]["info"]["run_id"]})).get("files", [])
    if not {"summary.yaml", "per_case_results.json"} <= {f["path"] for f in files}:
        raise ValueError("MLflow summary artifacts missing")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reports", type=Path, required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--mlflow", default="")
    a = p.parse_args()
    if a.mlflow:
        verify_mlflow(a.mlflow, a.run)
    failures = report_gate(a.reports, a.run)
    print(json.dumps({"status": "failed" if failures else "passed", "issues": failures}))
    raise SystemExit(bool(failures))
