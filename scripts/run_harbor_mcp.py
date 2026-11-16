"""Run a native Harbor skill/control pair with a fresh managed mock per side.

This first integration runs one native task and one repetition per PipelineRun.
Harbor owns agent execution and verification; trusted orchestration owns mocks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from abevalflow.harbor_extensions.harbor_evidence import validate_attempt
from abevalflow.harbor_extensions.mcp_connection import configure_job
from abevalflow.mcp_ci import (
    LABEL,
    Archive,
    Cluster,
    atomic_json,
    collect_logs,
    handoff,
    paths,
    quiesce,
    setup,
    submission_path,
)
from abevalflow.report import Provenance
from abevalflow.schemas import SubmissionMetadata
from scripts.analyze import build_analysis, parse_variant_trials, render_markdown
from scripts.generate_eval_config import build_variant_config, set_task_docker_image

TEXT_SUFFIXES = {".json", ".jsonl", ".log", ".txt", ".md", ".yaml", ".yml"}


def redact_text(text):
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "DATABASE_URL"):
        secret = os.environ.get(key)
        if secret and len(secret) > 4:
            text = text.replace(secret, "[REDACTED]")
    return text


def redact_tree(root):
    for path in Path(root).rglob("*"):
        if path.is_file() and not path.is_symlink() and path.suffix in TEXT_SUFFIXES:
            path.write_text(redact_text(path.read_text()))


def run_logged(command, log_file):
    """Preserve Harbor output and expose the same redacted lines in Task logs."""
    with Path(log_file).open("w") as log:
        with subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace"
        ) as process:
            for line in process.stdout:
                safe = redact_text(line)
                log.write(safe)
                log.flush()
                print(safe, end="", flush=True)
            return process.wait()


def prepare_task(source, dest, variant):
    if dest.exists():
        raise ValueError("refusing to reuse an existing Harbor task workspace")
    if any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("native task packages must not contain symlinks")
    shutil.copytree(source, dest)
    skills = dest / "environment" / "skills"
    if variant == "control":
        shutil.rmtree(skills, ignore_errors=True)
    elif not list(skills.rglob("SKILL.md")):
        raise ValueError("treatment has no skill")
    return dest


def archive_attempt(cluster, workspace, uid, run_name, target, out_dir, variant):
    root = paths(workspace, uid) / "attempts" / variant
    pods = quiesce(cluster, cluster.list("Pod", f"{LABEL}={uid}"))
    mocks = [p for p in pods if p.metadata.name == target["name"]]
    clients = [p for p in pods if (p.metadata.labels or {}).get("abevalflow.io/mcp-role") == "harbor-client"]
    if len(mocks) != 1 or len(clients) != 1:
        raise RuntimeError("missing exact mock or native Harbor child Pod evidence")
    if any(p.status.phase not in {"Succeeded", "Failed"} for p in [*mocks, *clients]):
        raise RuntimeError("attempt Pod snapshots are not terminal")
    collect_logs(cluster, [*mocks, *clients], root)
    atomic_json(
        root / "attempt-inventory.json",
        {
            "uid": uid,
            "variant": variant,
            "mock": target["name"],
            "client": clients[0].metadata.name,
            "client_image": clients[0].spec.containers[0].image,
            "pods": [{"name": p.metadata.name, "uid": p.metadata.uid} for p in [*mocks, *clients]],
        },
    )
    atomic_json(root / "target.json", target)
    readiness = paths(workspace, uid) / "readiness.json"
    ready = json.loads(readiness.read_text())
    if ready.get("uid") != uid or ready.get("ready") is not True:
        raise RuntimeError("missing successful run-specific MCP readiness")
    atomic_json(root / "readiness.json", ready)
    redact_tree(out_dir)
    redact_tree(root)
    archive = Archive(f"mcp-runs/{uid}/attempts/{variant}")
    objects = {}
    for directory in (root, out_dir):
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix in TEXT_SUFFIXES:
                key = str(path.relative_to(workspace))
                objects[key] = path.read_bytes()
                archive.put(key, objects[key])
    inventory = validate_attempt(objects, uid, variant, target["name"], require_cleanup=False)
    archive.commit(uid)
    # Upload/read-back failure above leaves all mock resources intact.
    selector = f"{LABEL}={uid}"
    for kind in ["Pod", "Service", "NetworkPolicy"]:
        for obj in cluster.list(kind, selector):
            cluster.delete(kind, obj.metadata.name)
    deadline = time.monotonic() + 90
    while any(cluster.list(kind, selector) for kind in ["Pod", "Service", "NetworkPolicy"]):
        if time.monotonic() >= deadline:
            raise TimeoutError("attempt resources still exist after archival")
        time.sleep(2)
    atomic_json(root / "cleanup.json", {"uid": uid, "variant": variant, "deleted": True, "pods": inventory["pods"]})
    archive.put(str((root / "cleanup.json").relative_to(workspace)), (root / "cleanup.json").read_bytes())
    archive.commit(uid)
    return archive.prefix


def verify_pair_archives(workspace, uid, submission):
    output = Path(workspace) / "eval-results" / submission
    pair = json.loads((output / "harbor-pairs.json").read_text())
    if pair.get("uid") != uid or {a["variant"] for a in pair["attempts"]} != {"control", "treatment"}:
        raise RuntimeError("invalid Harbor pair identity")
    if len(pair["attempts"]) != 2:
        raise RuntimeError("Harbor pair must contain exactly two attempts")
    if len({a["mock_name"] for a in pair["attempts"]}) != 2:
        raise RuntimeError("Harbor conditions must use distinct fresh mocks")
    pod_uids = set()
    for attempt in pair["attempts"]:
        variant = attempt["variant"]
        expected = f"mcp-runs/{uid}/attempts/{variant}"
        if attempt["archive_prefix"] != expected:
            raise RuntimeError("Harbor attempt archive prefix mismatch")
        archive = Archive(expected)

        def read(key):
            response = archive.client.get_object(archive.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()

        manifest = json.loads(read(expected + "/archive-complete.json"))
        if manifest.get("uid") != uid or manifest.get("complete") is not True:
            raise RuntimeError("incomplete Harbor attempt archive")
        objects = {}
        for entry in manifest["objects"]:
            key = entry["key"]
            if not key.startswith(expected + "/") or key in objects:
                raise RuntimeError("invalid Harbor archive object identity")
            value = read(key)
            if len(value) != entry["bytes"] or hashlib.sha256(value).hexdigest() != entry["sha256"]:
                raise RuntimeError("Harbor archive checksum mismatch")
            objects[key] = value
        inventory = validate_attempt(objects, uid, variant, attempt["mock_name"])
        current = {p["uid"] for p in inventory["pods"]}
        if current & pod_uids:
            raise RuntimeError("Harbor conditions reused a producer Pod")
        pod_uids.update(current)


def run(workspace, uid, run_name, submission_dir, case, image, model="claude-sonnet"):
    workspace = Path(workspace)
    source = submission_path(workspace, submission_dir)
    meta = SubmissionMetadata(**yaml.safe_load((source / "metadata.yaml").read_text()))
    if meta.experiment.n_trials != 1:
        raise ValueError("initial managed Harbor integration requires n_trials=1")
    if not meta.mcp or not meta.mcp.mock:
        raise ValueError("this profile requires a trusted managed mock")
    native = (source / "harbor-tasks" / case).resolve()
    if not native.is_relative_to(source) or not (native / "task.toml").is_file():
        raise ValueError("case must select one native Harbor task package")
    root = paths(workspace, uid)
    out = workspace / "eval-results" / meta.name
    order = ["control", "treatment"]
    random.Random(uid).shuffle(order)
    atomic_json(
        root / "harbor-inputs.json",
        {
            "uid": uid,
            "case": case,
            "model": model,
            "order": order,
            "files": {
                str(p.relative_to(native)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in native.rglob("*")
                if p.is_file() and not p.is_symlink()
            },
        },
    )
    cluster = Cluster(os.environ["POD_NAMESPACE"])
    attempts = []
    print(
        json.dumps({"event": "harbor_pair_started", "uid": uid, "case": case, "model": model, "order": order}),
        flush=True,
    )
    for index, variant in enumerate(order):
        print(json.dumps({"event": "harbor_attempt_started", "variant": variant}), flush=True)
        if index == 0:
            target = json.loads((root / "target.json").read_text())
        else:
            target = setup(cluster, workspace, uid, variant, run_name, meta.mcp.mock)
        if target.get("uid") != uid or target.get("mock") != meta.mcp.mock:
            raise ValueError("managed Harbor target does not match this run/submission")
        task = prepare_task(native, workspace / "harbor-tasks" / variant / case, variant)
        config = build_variant_config(
            meta, variant, str(task), "prebuilt", str(out / variant), image_ref=image, llm_model=model
        )
        set_task_docker_image(task, image)
        # Keep the completed child until trusted archival captures terminal logs/status.
        config["environment"]["delete"] = False
        config["agents"][0]["env"] = {
            "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
            "ANTHROPIC_BASE_URL": os.environ["ANTHROPIC_BASE_URL"],
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
        }
        configure_job(config, task, root / "target.json", uid, run_name)
        config["verifier"] = {
            "env": {
                "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
                "JUDGE_MODEL": model,
                "JUDGE_BASE_URL": os.environ["ANTHROPIC_BASE_URL"],
            }
        }
        config_file = root / (variant + "-job.yaml")
        config_file.write_text(yaml.safe_dump(config))
        (out / variant).mkdir(parents=True)
        log_file = out / variant / "harbor-process.log"
        exit_code = run_logged(["harbor", "run", "-c", str(config_file), "-y"], log_file)
        print(json.dumps({"event": "harbor_process_exited", "variant": variant, "exit_code": exit_code}), flush=True)
        redact_tree(out / variant)
        trials = parse_variant_trials(out / variant)
        if exit_code or len(trials) != 1 or trials[0].reward is None:
            raise RuntimeError(f"Harbor {variant} execution incomplete; inspect preserved logs")
        raw_trials = [
            json.loads(p.read_text())
            for p in (out / variant).rglob("result.json")
            if "verifier_result" in json.loads(p.read_text())
        ]
        if any(item.get("exception_info") for item in raw_trials):
            raise RuntimeError("Harbor reported a trial infrastructure exception")
        judges = list((out / variant).rglob("answer-judge.json"))
        if len(judges) != 1 or not isinstance(json.loads(judges[0].read_text()).get("passed"), bool):
            raise RuntimeError("answer judge did not complete; this is not a scored agent failure")
        trajectories = list((out / variant).rglob("trajectory.json"))
        if len(trajectories) != 1:
            raise RuntimeError("Harbor did not preserve exactly one native agent trajectory")
        archive_prefix = archive_attempt(cluster, workspace, uid, run_name, target, out / variant, variant)
        print(
            json.dumps({"event": "harbor_attempt_archived", "variant": variant, "reward": trials[0].reward}), flush=True
        )
        attempts.append(
            {
                "variant": variant,
                "reward": trials[0].reward,
                "process_exit_code": exit_code,
                "archive_prefix": archive_prefix,
                "mock_name": target["name"],
            }
        )
    atomic_json(
        out / "harbor-pairs.json",
        {
            "uid": uid,
            "case": case,
            "attempts": attempts,
            "note": "One pair is an integration smoke, not evidence of statistical uplift.",
        },
    )
    result = build_analysis(
        out,
        meta.name,
        provenance=Provenance(pipeline_run_id=uid, eval_engine="harbor"),
        eval_engine="harbor",
        llm_label=model,
    )
    report_dir = workspace / "reports" / meta.name
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "report.json").write_text(result.model_dump_json(indent=2))
    (report_dir / "report.md").write_text(render_markdown(result))
    handoff(
        workspace,
        uid,
        "harbor",
        {
            "treatment-mean-reward": str(result.summary.treatment.mean_reward),
            "control-mean-reward": str(result.summary.control.mean_reward),
            "recommendation": result.summary.recommendation.value,
            "results-dir": str(out),
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ["workspace", "uid", "run-name", "submission-dir", "case", "image"]:
        parser.add_argument("--" + arg, required=True)
    parser.add_argument("--model", default="claude-sonnet")
    args = parser.parse_args()
    run(args.workspace, args.uid, args.run_name, args.submission_dir, args.case, args.image, args.model)


if __name__ == "__main__":
    main()
