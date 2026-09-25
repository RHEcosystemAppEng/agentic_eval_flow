#!/usr/bin/env python3
"""Run the predeclared experiment in order, with no outcome-dependent retries."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--experiment", type=Path, required=True)
    p.add_argument("--namespace", required=True)
    p.add_argument("--base-run", type=Path, required=True)
    p.add_argument("--harness", type=Path, required=True)
    a = p.parse_args()
    root = a.experiment.resolve().parent
    plan = json.loads(a.experiment.read_text())
    scripts = Path(__file__).parent
    base = ["oc", "-n", a.namespace]
    results = []
    results_file = root / "runs.json"
    if results_file.exists():
        raise ValueError("experiment already started; preserve results and inspect failures")
    # Freeze both runtime overlays before any run starts.
    for arm in ("baseline", "candidate"):
        out = root / ("deployment-" + arm)
        subprocess.run(
            [
                sys.executable,
                str(scripts / "trigger_forge_controlled.py"),
                "--namespace",
                a.namespace,
                "--base-run",
                str(a.base_run),
                "--harness",
                str(a.harness),
                "--submission",
                str(root / plan["arms"][arm]["directory"]),
                "--output",
                str(out),
            ],
            check=True,
        )
        subprocess.run(
            base + ["apply", "--server-side", "--field-manager=forge-eval", "-f", str(out / "resources.json")],
            check=True,
        )
    for step in plan["order"]:
        arm = step["arm"]
        number = step["pair"]
        out = root / ("deployment-" + arm)
        run = json.loads(subprocess.check_output(base + ["create", "-f", str(out / "run.json"), "-o", "json"]))
        name = run["metadata"]["name"]
        print(f"pair={number} arm={arm} run={name}", flush=True)
        record = {**step, "run": name, "status": "running"}
        results.append(record)
        results_file.write_text(json.dumps(results, indent=2))
        deadline = time.monotonic() + 2400
        last = None
        while time.monotonic() < deadline:
            current = json.loads(subprocess.check_output(base + ["get", "pipelinerun", name, "-o", "json"]))
            condition = next(
                (c for c in current.get("status", {}).get("conditions", []) if c["type"] == "Succeeded"), {}
            )
            reason = condition.get("reason", "Pending")
            if reason != last:
                print(name, reason, flush=True)
                last = reason
            if condition.get("status") in ("True", "False"):
                break
            time.sleep(10)
        else:
            record["status"] = "timeout"
            results_file.write_text(json.dumps(results, indent=2))
            subprocess.run(
                base + ["patch", "pipelinerun", name, "--type=merge", "-p", '{"spec":{"status":"Cancelled"}}'],
                check=False,
            )
            record["error"] = "run exceeded 40 minutes; cancellation requested"
            results_file.write_text(json.dumps(results, indent=2))
            break
        record["status"] = reason
        record["started_at"] = current.get("status", {}).get("startTime")
        record["completed_at"] = current.get("status", {}).get("completionTime")
        taskruns = json.loads(
            subprocess.check_output(base + ["get", "taskrun", "-l", "tekton.dev/pipelineRun=" + name, "-o", "json"])
        )
        record["stages"] = {
            t["metadata"]["labels"]["tekton.dev/pipelineTask"]: next(
                (c["status"] for c in t.get("status", {}).get("conditions", []) if c["type"] == "Succeeded"), "Unknown"
            )
            for t in taskruns["items"]
        }
        # The final gate checks MLflow before checking expected quality failures.
        gate = subprocess.run(base + ["logs", name + "-gate-pod", "-c", "step-verify"], capture_output=True, text=True)
        record["mlflow_verified"] = False
        for line in gate.stdout.splitlines():
            try:
                if json.loads(line).get("status") in ("passed", "failed"):
                    record["mlflow_verified"] = True
            except (ValueError, AttributeError):
                pass
        results_file.write_text(json.dumps(results, indent=2))
        if "evaluate" not in record["stages"]:
            record["error"] = "preparation failed before agent execution; no case report exists"
            results_file.write_text(json.dumps(results, indent=2))
            break
        destination = root / "results" / name
        fetched = subprocess.run(
            [
                sys.executable,
                str(scripts / "fetch_forge_reports.py"),
                "--namespace",
                a.namespace,
                "--run",
                name,
                "--output",
                str(destination),
            ],
            check=False,
        )
        if fetched.returncode:
            record["error"] = "reports unavailable; infrastructure failed"
            results_file.write_text(json.dumps(results, indent=2))
            break
        dirs = list((destination / "reports").glob("*/" + name))
        if len(dirs) != 1:
            record["error"] = "cannot locate unique harness run report"
            results_file.write_text(json.dumps(results, indent=2))
            break
        record["directory"] = str(dirs[0])
        results_file.write_text(json.dumps(results, indent=2))
    comparison = subprocess.run(
        [
            sys.executable,
            str(scripts / "compare_forge_experiment.py"),
            "--experiment",
            str(a.experiment),
            "--runs",
            str(results_file),
            "--output",
            str(root / "comparison.json"),
        ],
        check=False,
    )
    return comparison.returncode


if __name__ == "__main__":
    raise SystemExit(main())
