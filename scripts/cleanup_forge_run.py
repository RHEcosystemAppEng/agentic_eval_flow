#!/usr/bin/env python3
"""Archive logs and reclaim one completed eval's pods/PVC after verified storage.

Requires an explicit run name and locally fetched storage attestation. Never
selects historical runs in bulk or deletes the PipelineRun/TaskRun records.
"""

import argparse
import json
import subprocess
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--namespace", required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--report", type=Path, required=True, help="Harness run directory with storage-attestation.json")
    p.add_argument("--archive", type=Path, required=True)
    a = p.parse_args()
    base = ["oc", "-n", a.namespace]

    def read(*args):
        return json.loads(subprocess.check_output(base + list(args) + ["-o", "json"]))

    attestation = json.loads((a.report / "storage-attestation.json").read_text())
    if attestation.get("run") != a.run or attestation.get("status") != "verified" or not attestation.get("objects"):
        raise ValueError("verified storage attestation required")
    run = read("get", "pipelinerun", a.run)
    if not any(
        c["type"] == "Succeeded" and c["status"] in ("True", "False")
        for c in run.get("status", {}).get("conditions", [])
    ):
        raise ValueError("evaluation still running")
    tasks = read("get", "taskrun", "-l", "tekton.dev/pipelineRun=" + a.run)
    store = [t for t in tasks["items"] if t["metadata"]["labels"].get("tekton.dev/pipelineTask") == "store"]
    if len(store) != 1 or not any(
        c["type"] == "Succeeded" and c["status"] == "True" for c in store[0]["status"]["conditions"]
    ):
        raise ValueError("durable storage Task did not succeed")
    pods = read("get", "pod", "-l", "tekton.dev/pipelineRun=" + a.run)
    if not pods["items"] or any(p["status"]["phase"] not in ("Succeeded", "Failed") for p in pods["items"]):
        raise ValueError("all selected pods must be completed")
    a.archive.mkdir(parents=True, exist_ok=True, mode=0o700)
    (a.archive / "pipelinerun.json").write_text(json.dumps(run, indent=2))
    (a.archive / "taskruns.json").write_text(json.dumps(tasks, indent=2))
    claims = set()
    for pod in pods["items"]:
        for volume in pod["spec"].get("volumes", []):
            if "persistentVolumeClaim" in volume:
                claims.add(volume["persistentVolumeClaim"]["claimName"])
        for container in pod["spec"]["containers"]:
            log = subprocess.check_output(base + ["logs", pod["metadata"]["name"], "-c", container["name"]], timeout=60)
            path = a.archive / (pod["metadata"]["name"] + "-" + container["name"] + ".log")
            path.write_bytes(log)
            path.chmod(0o600)
    # Only release protection on claims for which the pipeline already requested deletion.
    for claim in claims:
        if not read("get", "pvc", claim)["metadata"].get("deletionTimestamp"):
            raise ValueError("cleanup has not requested PVC deletion: " + claim)
    for pod in pods["items"]:
        subprocess.run(base + ["delete", "pod", pod["metadata"]["name"], "--wait=false"], check=True)
    for claim in claims:
        subprocess.run(base + ["wait", "--for=delete", "pvc/" + claim, "--timeout=60s"], check=True)
    print(json.dumps({"run": a.run, "reclaimed_claims": sorted(claims), "log_archive": str(a.archive)}))


if __name__ == "__main__":
    main()
