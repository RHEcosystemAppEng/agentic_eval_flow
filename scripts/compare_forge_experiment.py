#!/usr/bin/env python3
"""Compare the entire predeclared experiment; missing pairs are invalid."""

import argparse
import json
from pathlib import Path

from abevalflow.forge_compare import build_pair, compare


def verify_infrastructure(record):
    root = Path(record["directory"])
    storage = json.loads((root / "storage-attestation.json").read_text())
    if storage.get("run") != record["run"] or storage.get("status") != "verified" or not storage.get("objects"):
        raise ValueError("durable storage not verified: " + record["run"])
    stages = record.get("stages", {})
    if any(stages.get(k) != "True" for k in ("prepare", "evaluate", "analyze", "store")):
        raise ValueError("infrastructure stages did not succeed: " + record["run"])
    if record.get("mlflow_verified") is not True:
        raise ValueError("MLflow artifacts not verified: " + record["run"])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--experiment", type=Path, required=True)
    p.add_argument(
        "--runs", type=Path, required=True, help="JSON list of {pair, arm, directory}: directory contains cases/"
    )
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    plan = json.loads(a.experiment.read_text())
    runs = json.loads(a.runs.read_text())
    try:
        for record in runs:
            if "directory" not in record:
                raise ValueError(
                    record.get("run", "unknown run") + ": " + record.get("error", "report directory missing")
                )
        index = {(r["pair"], r["arm"]): Path(r["directory"]) for r in runs}
        expected = {(r["pair"], r["arm"]) for r in plan["order"]}
        if set(index) != expected or len(index) != len(runs):
            raise ValueError("missing, duplicate or unplanned experiment run")
        for record in runs:
            verify_infrastructure(record)
        pairs = []
        for number in range(plan["pairs"]):
            for case in plan["cases"]:
                row = build_pair(
                    index[number, "baseline"] / "cases" / case,
                    index[number, "candidate"] / "cases" / case,
                    case=case,
                    pair=number,
                    skill_path=plan["skill_path"],
                )
                for arm in ("baseline", "candidate"):
                    if row[arm]["skill"] != plan["arms"][arm]["skill_sha256"]:
                        raise ValueError("loaded skill differs from experiment")
                pairs.append(row)
        result = compare(pairs, policy=plan.get("policy", "improvement"))
        result["measurements"] = pairs
    except (ValueError, KeyError, OSError) as exc:
        result = {"verdict": "invalid_eval", "issues": [str(exc)]}
    result["planned_pairs"] = plan["pairs"]
    result["recorded_arms"] = len(runs)
    result["runs"] = [{k: r[k] for k in ("pair", "arm", "run", "status", "error") if k in r} for r in runs]
    a.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(result["verdict"])
    return int(result["verdict"] not in ("improved", "passed"))


if __name__ == "__main__":
    raise SystemExit(main())
