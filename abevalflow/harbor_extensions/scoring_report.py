"""Profile-local reporting for versioned continuous managed Harbor scores.

Generic analysis/database `passed` fields retain their historical reward > 0
meaning. This summary reports continuous scores and full credit independently.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from statistics import mean
from typing import Any

from abevalflow.harbor_extensions.harbor_evidence import validate_assessments

DIMENSIONS = ("task_completion", "protocol_compliance", "llm_judgment")
LEGACY_SEMANTICS = (
    "Generic passed, n_passed and pass_rate use reward > 0: they indicate "
    "nonzero credit, not full success. Use continuous mean scores and explicit "
    "full-credit counts for this managed Harbor profile."
)


def build_scoring_summary(
    breakdowns: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Summarize completed v2 assessments; reject missing or mixed scoring.

    Callers retain the original validated breakdowns as the detailed evidence.
    An infrastructure failure must remain unscored rather than become a zero.
    """
    if set(breakdowns) != {"control", "treatment"}:
        raise RuntimeError("continuous Harbor reporting requires both variants")
    variants = {}
    for variant in ("control", "treatment"):
        entries = breakdowns[variant]
        if not isinstance(entries, (list, tuple)) or not entries:
            raise RuntimeError("missing completed Harbor assessments: " + variant)
        trials = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, Mapping) or entry.get("scoring_version") != 2:
                raise RuntimeError("continuous Harbor reporting requires scoring_version=2")
            # Reuse the archive validator's public boundary rather than relaxing
            # numeric/check/weight agreement for presentation.
            try:
                task, protocol = (entry[key] for key in DIMENSIONS[:2])
                behavior_score = (task["score"] + protocol["score"]) / 2
                behavior = {
                    "scoring_version": 2,
                    "task_completion": task,
                    "protocol_compliance": protocol,
                    "score": behavior_score,
                    "passed": behavior_score == 1.0,
                    "failures": entry["failures"],
                }
                overall = validate_assessments(behavior, entry["llm_judgment"], dict(entry))
                dimensions = {
                    key: {"score": float(entry[key]["score"]), "full_credit": entry[key]["passed"]}
                    for key in DIMENSIONS
                }
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError("incomplete continuous Harbor assessment") from exc
            trials.append(
                {
                    "trial_index": index,
                    "overall_score": overall,
                    "full_credit": overall == 1.0,
                    "dimensions": dimensions,
                }
            )
        full_credit = sum(trial["full_credit"] for trial in trials)
        variants[variant] = {
            "n_scored": len(trials),
            "mean_overall_score": mean(trial["overall_score"] for trial in trials),
            "dimension_means": {key: mean(trial["dimensions"][key]["score"] for trial in trials) for key in DIMENSIONS},
            "n_full_credit": full_credit,
            "full_credit_rate": full_credit / len(trials),
            "trials": trials,
        }
    return {
        "scoring_version": 2,
        "primary_metric": "mean_overall_score",
        "score_range": [0, 1],
        "weights": {key: 1 / 3 for key in DIMENSIONS},
        "legacy_nonzero_credit_semantics": LEGACY_SEMANTICS,
        "variants": variants,
    }


def _display_score(value: float) -> str:
    rounded = f"{value:.6f}".rstrip("0").rstrip(".")
    if value < 1 and rounded == "1":
        return "<1"
    if value > 0 and rounded == "0":
        return ">0"
    return rounded


def render_scoring_note(summary: Mapping[str, Any]) -> str:
    """Append profile semantics and continuous/full-credit metrics to a report."""
    lines = [
        "",
        "## Continuous managed Harbor scoring",
        "",
        LEGACY_SEMANTICS,
        "",
        "Overall score is the equally weighted mean of task completion, protocol "
        "compliance and the full-trace LLM judgment, each from 0 to 1. "
        "Full credit means an overall score of exactly 1; it does not gate partial reward.",
        "",
    ]
    for variant in ("control", "treatment"):
        data = summary["variants"][variant]
        dimensions = data["dimension_means"]
        lines.append(
            f"- **{variant.title()}**: mean overall {_display_score(data['mean_overall_score'])}; "
            f"task completion {_display_score(dimensions['task_completion'])}; "
            f"protocol compliance {_display_score(dimensions['protocol_compliance'])}; "
            f"LLM judgment {_display_score(dimensions['llm_judgment'])}; "
            f"full credit {data['n_full_credit']}/{data['n_scored']}."
        )
    lines.extend(["", "One pair is an integration smoke and does not establish statistical skill benefit.", ""])
    return "\n".join(lines)
