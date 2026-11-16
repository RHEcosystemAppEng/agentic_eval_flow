"""Download a run's pod-log archive without requiring mc on the MinIO server."""

import argparse
import base64
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from minio import Minio


def download(run, namespace, output, uid=None):
    def oc(*args):
        return subprocess.check_output(["oc", "-n", namespace, "--request-timeout=30s", *args], text=True)

    secret = json.loads(oc("get", "secret", "minio-credentials", "-o", "json"))["data"]
    values = {key: base64.b64decode(value).decode() for key, value in secret.items()}
    endpoint = urlparse(os.environ.get("MINIO_ENDPOINT", values["endpoint-url"]))
    client = Minio(
        endpoint.netloc or endpoint.path,
        access_key=values["root-user"],
        secret_key=values["root-password"],
        secure=endpoint.scheme == "https",
    )
    bucket = os.environ.get("MINIO_BUCKET", "ab-eval-reports")
    uid = uid or oc("get", "pipelinerun", run, "--ignore-not-found", "-o", "jsonpath={.metadata.uid}").strip()
    if not uid:
        matches = []
        for item in client.list_objects(bucket, prefix="mcp-runs/", recursive=True):
            if not item.object_name.endswith("/identity.json"):
                continue
            response = client.get_object(bucket, item.object_name)
            try:
                identity = json.loads(response.read())
            finally:
                response.close()
                response.release_conn()
            if identity.get("run_name") == run:
                matches.append(identity["uid"])
        if len(set(matches)) > 1:
            raise RuntimeError("Run name was reused; pass --uid to select the intended archive")
        uid = matches[0] if matches else None
    prefixes = ([f"mcp-runs/{uid}/pod-logs/"] if uid else []) + [f"pod-logs/{run}/"]
    objects = []
    for prefix in prefixes:
        objects = list(client.list_objects(bucket, prefix=prefix, recursive=True))
        if objects:
            break
    if not objects:
        # Original archive layout remains readable for historical runs.
        objects = [
            item
            for item in client.list_objects(bucket, recursive=True)
            if f"debug/pod-logs/{run}/" in item.object_name and item.object_name.endswith(".log")
        ]
    if not objects:
        raise RuntimeError("No archived pod logs found")
    output.mkdir(parents=True, exist_ok=True)
    for obj in objects:
        client.fget_object(bucket, obj.object_name, str(output / Path(obj.object_name).name))
    print(f"Downloaded {len(objects)} log files to {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run")
    parser.add_argument("namespace", nargs="?", default="")
    parser.add_argument("output", nargs="?", type=Path)
    parser.add_argument("--uid", help="Exact run UID, including after PipelineRun pruning")
    args = parser.parse_args()
    namespace = (
        args.namespace
        or subprocess.check_output(
            ["oc", "config", "view", "--minify", "-o", "jsonpath={..namespace}"], text=True
        ).strip()
    )
    if not namespace:
        parser.error("Pass a namespace or select one in the oc context")
    download(args.run, namespace, args.output or Path(args.run + "-logs"), args.uid)


if __name__ == "__main__":
    main()
