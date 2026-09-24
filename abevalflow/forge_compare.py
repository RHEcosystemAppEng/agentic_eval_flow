"""Paired Forge quality and zero-output-token-increase acceptance gate.

Run: python -m abevalflow.forge_compare pairs.json --output comparison.json
Each pair has shared immutable provenance and independently measured arms.
"""

import argparse
import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _p95(values):
    return sorted(values)[max(0, math.ceil(len(values) * 0.95) - 1)]


def build_pair(baseline_dir, candidate_dir, *, case, pair, skill_path):
    """Construct paired input from captured artifacts, refusing confounded arms."""

    def load(root):
        root = Path(root)
        return json.loads((root / "run_result.json").read_text()), json.loads((root / "provenance.json").read_text())

    br, bp = load(baseline_dir)
    cr, cp = load(candidate_dir)

    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    for key in ("image", "model", "effort", "prompt_sha256", "harness_files", "model_config_sha256"):
        if not bp.get(key) or bp[key] != cp.get(key):
            raise ValueError("confounded pair: " + key)

    def inputs(p):
        ignore = {v["path"] for v in p.get("skill_overrides", {}).values()}
        return {k: v for k, v in p["initial_files"].items() if k not in ignore}

    if inputs(bp) != inputs(cp):
        raise ValueError("confounded initial fixture/state")
    before = {k: v for k, v in bp["loaded_files"].items() if k != skill_path}
    after = {k: v for k, v in cp["loaded_files"].items() if k != skill_path}
    if before != after:
        raise ValueError("more than the intended skill changed")

    def arm(result, provenance):
        status = result.get("evaluation_status")
        return {
            "valid": status in ("passed", "quality_failed"),
            "usage_complete": result.get("usage", {}).get("complete") is True,
            "output_tokens": result.get("usage", {}).get("output"),
            "input_tokens": result.get("usage", {}).get("input"),
            "reasoning_tokens": result.get("usage", {}).get("reasoning"),
            "cache_read_tokens": result.get("usage", {}).get("cache_read"),
            "model_requests": result.get("usage", {}).get("requests"),
            "tool_calls": result.get("usage", {}).get("tool_calls"),
            "agent_duration_s": result.get("duration_s"),
            "quality": int(status == "passed"),
            "critical_pass": status == "passed",
            "skill": provenance["loaded_files"][skill_path],
        }

    return {
        "case": case,
        "pair": str(pair),
        "provenance": {
            "image": bp["image"],
            "model": digest([bp["model"], bp["model_config_sha256"], bp["effort"]]),
            "fixture": digest(inputs(bp)),
            "runtime": digest(bp["harness_files"]),
            "prompt": bp["prompt_sha256"],
            "clock": "fixture-or-draft-fixed-clock-v1",
        },
        "baseline": arm(br, bp),
        "candidate": arm(cr, cp),
    }


def compare(rows, *, bootstrap_samples=4000):
    errors = []
    seen = set()
    groups = defaultdict(list)
    common = None
    skill_pairs = set()
    for row in rows:
        try:
            key = (row["case"], row["pair"])
            if key in seen:
                raise ValueError("duplicate pair")
            seen.add(key)
            p = row["provenance"]
            required = ("image", "model", "fixture", "runtime", "prompt", "clock")
            if any(not isinstance(p.get(k), str) or not p[k] for k in required):
                raise ValueError("missing immutable provenance")
            shared = tuple(p[k] for k in ("image", "model", "runtime", "clock"))
            if common is not None and shared != common:
                raise ValueError("mixed runtime/model provenance")
            common = shared
            for arm in ("baseline", "candidate"):
                a = row[arm]
                if a.get("valid") is not True or a.get("usage_complete") is not True:
                    raise ValueError("invalid execution or incomplete usage")
                if not _number(a.get("output_tokens")) or not _number(a.get("quality")):
                    raise ValueError("invalid numeric measurement")
                if type(a.get("critical_pass")) is not bool or not a.get("skill"):
                    raise ValueError("missing correctness or skill provenance")
            skill_pairs.add((row["baseline"]["skill"], row["candidate"]["skill"]))
            groups[row["case"]].append(row)
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(str(exc))
    for case, pairs in groups.items():
        if len({(p["provenance"]["fixture"], p["provenance"]["prompt"]) for p in pairs}) != 1:
            errors.append("fixture/prompt drift: " + case)
    if len(skill_pairs) != 1:
        errors.append("mixed or missing skill arms")
    if errors or not rows:
        return {"verdict": "invalid_eval", "issues": errors or ["no pairs"]}
    baseline = [r["baseline"]["output_tokens"] for r in rows]
    candidate = [r["candidate"]["output_tokens"] for r in rows]
    deltas = [c - b for b, c in zip(baseline, candidate)]
    report = {
        "pairs": len(rows),
        "cases": len(groups),
        "issues": [],
        "output_tokens": {
            "baseline_mean": mean(baseline),
            "candidate_mean": mean(candidate),
            "mean_delta": mean(deltas),
            "baseline_p95": _p95(baseline),
            "candidate_p95": _p95(candidate),
            "percent_change": 100 * mean(deltas) / mean(baseline) if mean(baseline) else None,
        },
        "per_case": {
            case: {
                "pairs": len(rs),
                "baseline_mean": mean(r["baseline"]["output_tokens"] for r in rs),
                "candidate_mean": mean(r["candidate"]["output_tokens"] for r in rs),
                "baseline_p95": _p95([r["baseline"]["output_tokens"] for r in rs]),
                "candidate_p95": _p95([r["candidate"]["output_tokens"] for r in rs]),
                "mean_delta": mean(r["candidate"]["output_tokens"] - r["baseline"]["output_tokens"] for r in rs),
                "quality_delta": mean(r["candidate"]["quality"] - r["baseline"]["quality"] for r in rs),
            }
            for case, rs in groups.items()
        },
    }
    for case in report["per_case"].values():
        case["percent_change"] = 100 * case["mean_delta"] / case["baseline_mean"] if case["baseline_mean"] else None
    if any(
        r["candidate"]["critical_pass"] is not True or r["candidate"]["quality"] < r["baseline"]["quality"]
        for r in rows
    ):
        report["verdict"] = "quality_failed"
    elif (
        mean(deltas) > 0
        or _p95(candidate) > _p95(baseline)
        or any(v["mean_delta"] > 0 for v in report["per_case"].values())
    ):
        report["verdict"] = "token_regression"
    elif any(len(rs) < 3 for rs in groups.values()):
        report["verdict"] = "inconclusive"
        report["issues"].append("at least three paired repetitions per case required")
    else:
        # Resample within each case; preserve the case mix and baseline/candidate pairing.
        rng = random.Random(20260924)
        samples = []
        for _ in range(bootstrap_samples):
            sampled = []
            for rs in groups.values():
                ds = [r["candidate"]["output_tokens"] - r["baseline"]["output_tokens"] for r in rs]
                sampled.extend(rng.choices(ds, k=len(ds)))
            samples.append(mean(sampled))
        upper = _p95(samples)
        report["output_tokens"]["paired_mean_upper_95"] = upper
        if upper > 0:
            report["verdict"] = "inconclusive"
        elif mean(r["candidate"]["quality"] - r["baseline"]["quality"] for r in rows) > 0:
            report["verdict"] = "improved"
        else:
            report["verdict"] = "no_quality_improvement"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pairs", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = compare(json.loads(args.pairs.read_text()))
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(result["verdict"])
    return 0 if result["verdict"] == "improved" else 1


if __name__ == "__main__":
    raise SystemExit(main())
