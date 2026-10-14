#!/usr/bin/env python3
"""Read retained evaluation reports without modifying their PVC."""

import argparse
import io
import json
import subprocess
import tarfile
import uuid
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--namespace", required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    base = ["oc", "-n", a.namespace]
    source = json.loads(subprocess.check_output(base + ["get", "pod", a.run + "-evaluate-pod", "-o", "json"]))
    volume = next(v for v in source["spec"]["volumes"] if v.get("persistentVolumeClaim"))
    name = "forge-report-reader-" + uuid.uuid4().hex[:8]
    pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": a.namespace},
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "nodeName": source["spec"]["nodeName"],
            "containers": [
                {
                    "name": "reader",
                    "image": "registry.access.redhat.com/ubi9/python-311:9.6",
                    "command": ["sleep", "300"],
                    "resources": {
                        "requests": {"cpu": "10m", "memory": "64Mi"},
                        "limits": {"cpu": "200m", "memory": "256Mi"},
                    },
                    "volumeMounts": [{"name": "reports", "mountPath": "/reports", "readOnly": True}],
                }
            ],
            "volumes": [
                {
                    "name": "reports",
                    "persistentVolumeClaim": {
                        "claimName": volume["persistentVolumeClaim"]["claimName"],
                        "readOnly": True,
                    },
                }
            ],
        },
    }
    subprocess.run(base + ["create", "-f", "-"], input=json.dumps(pod).encode(), check=True, capture_output=True)
    try:
        subprocess.run(
            base + ["wait", "pod/" + name, "--for=condition=Ready", "--timeout=60s"], check=True, capture_output=True
        )
        data = subprocess.check_output(
            base + ["exec", name, "--", "tar", "-czf", "-", "-C", "/reports", "reports"], timeout=60
        )
        a.output.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            archive.extractall(a.output, filter="data")
        print("Reports saved to", a.output)
    finally:
        subprocess.run(base + ["delete", "pod", name, "--wait=false"], check=False, capture_output=True)


if __name__ == "__main__":
    main()
