#!/usr/bin/env python3
"""Freeze a skill-only experiment before running either arm; no cluster mutation."""

import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import yaml


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--submission", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--baseline", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--case", action="append", required=True)
    p.add_argument("--pairs", type=int, default=3)
    p.add_argument("--skill", choices=["forge-drafts", "daily-briefing"], default="forge-drafts")
    a = p.parse_args()
    if not 3 <= a.pairs <= 10:
        raise ValueError("predeclare 3–10 pairs; do not stop early for a favorable result")
    if a.output.exists():
        raise ValueError("experiment directory exists; preserve the original plan")
    if any(not re.fullmatch("[a-f0-9]{40}", r) for r in (a.baseline, a.candidate)):
        raise ValueError("source revisions must be immutable full commit IDs")
    source = (
        "tools/forge-draft/SKILL.md"
        if a.skill == "forge-drafts"
        else "agents/chief-of-staff/workspace/skills/daily-briefing/SKILL.md"
    )
    cfg = yaml.safe_load((a.submission / "eval.yaml").read_text())
    for case in a.case:
        if not (a.submission / cfg["dataset"]["path"] / case / "input.yaml").is_file():
            raise ValueError("unknown case: " + case)
    a.output.mkdir(parents=True)
    plan = {
        "version": 1,
        "pairs": a.pairs,
        "cases": a.case,
        "skill_path": "skills/" + a.skill + "/SKILL.md",
        "arms": {},
        "order": [],
        "maximum_pairs": a.pairs,
    }
    for arm, ref in [("baseline", a.baseline), ("candidate", a.candidate)]:
        response = json.loads(
            subprocess.check_output(["gh", "api", f"repos/rh-forge/openclaw-saw-image/contents/{source}?ref={ref}"])
        )
        content = base64.b64decode(response["content"])
        digest = hashlib.sha256(content).hexdigest()
        root = a.output / arm
        shutil.copytree(a.submission, root)
        arm_cfg = yaml.safe_load((root / "eval.yaml").read_text())
        for case in (root / cfg["dataset"]["path"]).iterdir():
            if case.is_dir() and case.name not in a.case:
                shutil.rmtree(case)
            elif case.is_dir():
                (case / "eval").mkdir(exist_ok=True)
                (case / "eval/skill-override.md").write_bytes(content)
        arm_cfg["runner"]["settings"]["forge_skill_overrides"] = {
            a.skill: {"path": "eval/skill-override.md", "sha256": digest}
        }
        arm_cfg["runner"]["settings"]["forge_experiment"] = {"arm": arm, "source_revision": ref, "skill_sha256": digest}
        (root / "eval.yaml").write_text(yaml.safe_dump(arm_cfg, sort_keys=False))
        plan["arms"][arm] = {"directory": arm, "source_revision": ref, "skill_sha256": digest}
    for number in range(a.pairs):
        for arm in ["baseline", "candidate"] if number % 2 == 0 else ["candidate", "baseline"]:
            plan["order"].append({"pair": number, "arm": arm})
    (a.output / "experiment.json").write_text(json.dumps(plan, indent=2) + "\n")
    print("Frozen experiment:", a.output / "experiment.json")


if __name__ == "__main__":
    main()
