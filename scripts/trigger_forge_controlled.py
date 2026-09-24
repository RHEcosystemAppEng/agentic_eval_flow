#!/usr/bin/env python3
"""Trigger a content-addressed eval overlay; credentials remain Secret references.

Requires an already installed AEH OpenShell pipeline and configured eval credentials.
Copies namespace Tasks under unique names; never changes the live Forge deployment.
"""

import argparse
import base64
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path

import yaml


def rewrite_clones(script):
    # git clone --branch accepts branch/tag but not immutable commit IDs.
    pattern = r'git clone --depth 1 --branch "([^"\n]+)"\s*(?:\\\n\s*)?"([^"\n]+)" "([^"\n]+)"'

    def replace(m):
        ref, url, dest = m.groups()
        return (
            f'git init "{dest}"\n'
            f'git -C "{dest}" remote add origin "{url}"\n'
            f'git -C "{dest}" fetch --depth 1 origin "{ref}"\n'
            f'git -C "{dest}" checkout --detach FETCH_HEAD'
        )

    script = re.sub(pattern, replace, script)
    # Later stages already have the pinned checkout plus the verified overlay.
    # Do not refetch the same commit over the network just to analyze/store it.
    existing = (
        r'(?m)^([ \t]*)git -C "([^"\n]+)" fetch origin "([^"\n]+)" --depth 1\n'
        r'[ \t]*git -C "\2" -c advice.detachedHead=false checkout FETCH_HEAD'
    )

    def reuse(m):
        indent, directory, ref = m.groups()
        return (
            f'{indent}if [ "$(git -C "{directory}" rev-parse HEAD)" != "{ref}" ]; then\n'
            f'{indent}  git -C "{directory}" fetch origin "{ref}" --depth 1\n'
            f'{indent}  git -C "{directory}" -c advice.detachedHead=false checkout FETCH_HEAD\n'
            f"{indent}fi"
        )

    script = re.sub(existing, reuse, script)
    # Bound every Git command including TCP connection stalls.
    script = re.sub(r"(?m)^(\s*)git ", r"\1timeout 180 git -c http.lowSpeedLimit=1 -c http.lowSpeedTime=30 ", script)

    def retry_fetch(m):
        indent, command = m.groups()
        return (
            f"{indent}for forge_fetch_attempt in 1 2 3; do\n"
            f"{indent}  if {command}; then break; fi\n"
            f'{indent}  if [ "$forge_fetch_attempt" = "3" ]; then exit 1; fi\n'
            f'{indent}  echo "Transient Git fetch failure; retry $forge_fetch_attempt/3" >&2\n'
            f"{indent}  sleep 2\n{indent}done"
        )

    script = re.sub(r"(?m)^([ \t]*)(timeout 180 git [^\n]*\bfetch\b[^\n]*)$", retry_fetch, script)
    return script


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--base-run", type=Path, required=True)
    parser.add_argument("--harness", type=Path, required=True)
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    flow = Path(__file__).resolve().parents[1]
    namespace = args.namespace

    def oc(*cmd, **kw):
        return subprocess.check_output(["oc", "-n", namespace, *cmd], text=True, **kw)

    def get(kind, name):
        return json.loads(oc("get", kind, name, "-o", "json"))

    run = json.loads(args.base_run.read_text())
    run["metadata"] = {"generateName": "forge-controlled-", "namespace": namespace}
    run.pop("status", None)
    params = {p["name"]: p for p in run["spec"]["params"]}
    pipeline = get("pipeline", run["spec"]["pipelineRef"]["name"])
    for p in pipeline["spec"]["params"]:
        if p["name"] not in params and "default" in p:
            value = {"name": p["name"], "value": p["default"]}
            params[p["name"]] = value
            run["spec"]["params"].append(value)
    # Credentials belong in namespace Secrets, not reusable run manifests.
    # Deterministic suites need no judge API key; the agent uses its provider.
    if "llm-api-key" in params:
        params["llm-api-key"]["value"] = ""
    for name in ("revision", "pipeline-repo-revision", "agent-eval-harness-repo-revision"):
        url_name = {
            "revision": "repo-url",
            "pipeline-repo-revision": "pipeline-repo-url",
            "agent-eval-harness-repo-revision": "agent-eval-harness-repo-url",
        }[name]
        ref = params[name]["value"]
        url = params[url_name]["value"]
        if not re.fullmatch(r"[a-f0-9]{40}", ref):
            result = subprocess.check_output(
                ["git", "ls-remote", url, "refs/heads/" + ref], text=True, timeout=60
            ).splitlines()
            if not result:
                result = subprocess.check_output(
                    ["git", "ls-remote", url, "refs/tags/" + ref], text=True, timeout=60
                ).splitlines()
            if len(result) != 1:
                raise ValueError("cannot uniquely resolve " + name)
            params[name]["value"] = result[0].split()[0]
    cfg = yaml.safe_load((args.submission / "eval.yaml").read_text())
    allowed_providers = cfg.get("runner", {}).get("settings", {}).get("forge_providers")
    if allowed_providers:
        params["openshell-provider"]["value"] = ",".join(allowed_providers)
    dataset = args.submission / cfg["dataset"]["path"]
    cfg["dataset"]["path"] = "cases-controlled"
    files = {}
    for p in args.harness.rglob("*"):
        if (
            p.is_file()
            and p.suffix in (".py", ".mjs")
            and p.relative_to(args.harness).parts[0] == "agent_eval"
            and "__pycache__" not in p.parts
        ):
            files["_harness/" + p.relative_to(args.harness).as_posix()] = p.read_bytes()
    # Preserve the existing prepare submission contract; setup replaces only
    # its eval config/cases after preparation. Content hashes capture this overlay.
    prefix = "submissions/" + params["submission-dir"]["value"] + "/"
    files[prefix + "eval.yaml"] = yaml.safe_dump(cfg, sort_keys=False).encode()
    for case in sorted(dataset.iterdir()):
        if not case.is_dir() or (args.case and case.name not in args.case):
            continue
        for p in case.rglob("*"):
            if p.is_file():
                files[prefix + "cases-controlled/" + case.name + "/" + p.relative_to(case).as_posix()] = p.read_bytes()
    if args.case and set(args.case) - {p.name for p in dataset.iterdir()}:
        raise ValueError("unknown selected case")
    for relative in ["scripts/publish.py", "abevalflow/forge_storage.py", "scripts/forge_gate.py"]:
        if (flow / relative).exists():
            files["_pipeline/" + relative] = (flow / relative).read_bytes()
    # Include render logic and inherited Task specs in resource identity, so a
    # later trigger cannot mutate a running experiment with the same overlay.
    templates = {stage["name"]: get("task", stage["taskRef"]["name"]) for stage in pipeline["spec"]["tasks"]}
    files["eval-render-inputs.json"] = json.dumps(
        {
            "trigger_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "pipeline": pipeline["spec"],
            "tasks": {k: v["spec"] for k, v in templates.items()},
        },
        sort_keys=True,
    ).encode()
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
    files["eval-overlay-manifest.json"] = json.dumps(manifest, sort_keys=True).encode()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        for name, data in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
    data = gzip.compress(archive.getvalue(), mtime=0)
    if len(data) > 700000:
        raise ValueError("overlay exceeds ConfigMap budget")
    digest = hashlib.sha256(data).hexdigest()
    suffix = digest[:12]
    cm = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "forge-eval-" + suffix, "namespace": namespace},
        "immutable": True,
        "binaryData": {"overlay.tar.gz": base64.b64encode(data).decode()},
    }
    resources = [cm]
    tasks = {}
    for stage in pipeline["spec"]["tasks"]:
        kind = stage["name"]
        task = templates[kind]
        task["metadata"] = {"name": "forge-" + kind + "-" + suffix, "namespace": namespace}
        task.pop("status", None)
        for step in task["spec"]["steps"]:
            if "script" in step:
                step["script"] = rewrite_clones(step["script"])
        if kind == "evaluate":
            volumes = task["spec"].setdefault("volumes", [])
            volumes[:] = [v for v in volumes if v["name"] != "forge-bootstrap-compat"]
            volumes.append({"name": "forge-bootstrap-compat", "configMap": {"name": cm["metadata"]["name"]}})
            setup = next(s for s in task["spec"]["steps"] if s["name"] == "setup")
            mounts = setup.setdefault("volumeMounts", [])
            mounts[:] = [m for m in mounts if m["name"] != "forge-bootstrap-compat"]
            mounts.append({"name": "forge-bootstrap-compat", "mountPath": "/opt/eval-compat", "readOnly": True})
            setup["script"] = setup["script"].split('if [ "$(params.eval-engine)" = "aeh_openshell_openclaw" ]; then')[
                0
            ]
            setup["script"] += (
                f'\necho "{digest}  /opt/eval-compat/overlay.tar.gz" | sha256sum -c -\n'
                'tar -xzf /opt/eval-compat/overlay.tar.gz -C "$(workspaces.source.path)"\n'
            )
            execution = next(s for s in task["spec"]["steps"] if s["name"] == "aeh-openshell-eval")
            execution["script"] = execution["script"].replace(
                'exit "$AEH_EXIT"', 'echo "Failure recorded; continue to durable storage before final gate"'
            )
        stage["taskRef"]["name"] = task["metadata"]["name"]
        tasks[kind] = task["metadata"]["name"]
        resources.append(task)
    pipeline["metadata"] = {"name": "forge-eval-" + suffix, "namespace": namespace}
    pipeline.pop("status", None)
    # Retain PVC when a task/storage fails; a successful delete request is not
    # evidence of reclamation. Never delete historical pipeline pods here.
    for task in pipeline["spec"].get("finally", []):
        if task["name"] == "cleanup":
            task["when"] = [{"input": "$(tasks.store.status)", "operator": "in", "values": ["Succeeded"]}]
    source_workspace = next(
        w["workspace"]
        for t in pipeline["spec"]["tasks"]
        if t["name"] == "evaluate"
        for w in t.get("workspaces", [])
        if w["name"] == "source"
    )
    pipeline["spec"]["tasks"].append(
        {
            "name": "gate",
            "runAfter": ["store"],
            "workspaces": [{"name": "source", "workspace": source_workspace}],
            "taskSpec": {
                "workspaces": [{"name": "source"}],
                "steps": [
                    {
                        "name": "verify",
                        "image": "registry.access.redhat.com/ubi9/python-311:9.6",
                        "script": '#!/usr/bin/env bash\nset -euo pipefail\nmlflow=""\n'
                        'if [ "$(params.enable-mlflow)" = "true" ]; then mlflow="$(params.mlflow-tracking-uri)"; fi\n'
                        'python "$(workspaces.source.path)/_pipeline/scripts/forge_gate.py" '
                        '--reports "$(workspaces.source.path)/reports" '
                        '--run "$(context.pipelineRun.name)" --mlflow "$mlflow"\n',
                    }
                ],
            },
        }
    )
    resources.append(pipeline)
    run["spec"]["pipelineRef"]["name"] = pipeline["metadata"]["name"]
    (args.output / "resources.json").write_text(
        json.dumps({"apiVersion": "v1", "kind": "List", "items": resources}, indent=2)
    )
    (args.output / "run.json").write_text(json.dumps(run, indent=2))
    (args.output / "overlay-manifest.json").write_text(json.dumps({"sha256": digest, "files": manifest}, indent=2))
    if args.submit:
        print(oc("apply", "--server-side", "--field-manager=forge-eval", "-f", str(args.output / "resources.json")))
        print(oc("create", "-f", str(args.output / "run.json")))
    else:
        print("Rendered resources and run to", args.output)


if __name__ == "__main__":
    main()
