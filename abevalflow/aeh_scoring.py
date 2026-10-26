"""Shared AEH scoring helpers used by the engine adapter and aggregate script.

Keeps pairwise win-rate / gate logic and numeric judge pass/fail heuristics
aligned across call sites (engine, aggregate_aeh, report consumers).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DEFAULT_AEH_THRESHOLD = 0.5


def resolve_evaluation_threshold(
    metadata_path: Path | str | None = None,
    *,
    default: float = DEFAULT_AEH_THRESHOLD,
) -> float:
    """Read ``gate_policy.gates.evaluation.threshold`` from metadata.yaml.

    Returns ``default`` when metadata is missing or the threshold is unset.
    """
    if metadata_path is None:
        return default
    path = Path(metadata_path)
    if not path.is_file():
        return default
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return default
    if not isinstance(data, dict):
        return default
    gate_policy = data.get("gate_policy") or {}
    if not isinstance(gate_policy, dict):
        return default
    gates = gate_policy.get("gates") or {}
    if not isinstance(gates, dict):
        return default
    evaluation = gates.get("evaluation") or {}
    if not isinstance(evaluation, dict):
        return default
    threshold = evaluation.get("threshold")
    if threshold is None:
        return default
    try:
        return float(threshold)
    except (TypeError, ValueError):
        return default


def pairwise_outcome(
    wins_a: int,
    wins_b: int,
    ties: int = 0,
    errors: int = 0,
    *,
    threshold: float = DEFAULT_AEH_THRESHOLD,
) -> dict[str, Any]:
    """Compute honest pairwise win-rate and pass/fail.

    - Denominator includes ties and errors (errors are non-wins).
    - All-ties (no decisive outcomes, no errors) still passes.
    - All-errors does not get the all-ties exception.
    """
    wins_a = int(wins_a or 0)
    wins_b = int(wins_b or 0)
    ties = int(ties or 0)
    errors = int(errors or 0)
    total = wins_a + wins_b + ties + errors
    decisive = wins_a + wins_b
    win_rate = (wins_a / total) if total > 0 else 0.0
    all_ties = decisive == 0 and ties > 0 and errors == 0
    passed = all_ties or (win_rate >= threshold)
    return {
        "wins_a": wins_a,
        "wins_b": wins_b,
        "ties": ties,
        "errors": errors,
        "total": total,
        "decisive": decisive,
        "win_rate": win_rate,
        "all_ties": all_ties,
        "passed": passed,
        "threshold": threshold,
        "recommendation": "pass" if passed else "fail",
    }


def numeric_judge_passes(value: int | float) -> bool:
    """Whether a numeric judge value counts as a pass.

    Scale detection:
    - Integer 1–5 → Likert; pass when ``>= 3``
    - Float in [0, 1] → normalized; pass when ``>= 0.5``
    - Float in (1, 5] → Likert; pass when ``>= 3``
    - Otherwise → normalized fallback (``>= 0.5``)

    Callers must handle booleans separately (``bool`` is a subclass of ``int``).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and 1 <= value <= 5:
        return value >= 3
    v = float(value)
    if 0.0 <= v <= 1.0:
        return v >= 0.5
    if 1.0 < v <= 5.0:
        return v >= 3.0
    return v >= 0.5


def numeric_judge_is_low(value: int | float) -> bool:
    """Inverse of :func:`numeric_judge_passes` for finding extraction."""
    if isinstance(value, bool):
        return not value
    return not numeric_judge_passes(value)


def load_judge_score_ranges(config_path: Path | str | None) -> dict[str, tuple[float, float]]:
    """Read each judge's declared ``score_range`` from an eval*.yaml config.

    Lets callers (``the AEH result aggregator``, report consumers) disambiguate a
    bare numeric judge value like ``1`` using the judge's own declared scale
    instead of guessing from the value's Python type/magnitude -- see
    :func:`_normalize_numeric_value`. A judge declared ``score_range: [0, 1]``
    that returns the JSON integer ``1`` (a perfect score, not a float ``1.0``)
    would otherwise be misread as the floor of a 1-5 Likert rubric.

    Returns ``{}`` when the config is missing, unreadable, declares no
    ``judges``, or no judge declares a ``score_range``. Callers should treat
    any judge name absent from the result as "unknown scale" and fall back to
    the existing int/float heuristic for it.
    """
    if config_path is None:
        return {}
    path = Path(config_path)
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(data, dict):
        return {}
    judges = data.get("judges")
    if not isinstance(judges, list):
        return {}
    ranges: dict[str, tuple[float, float]] = {}
    for judge in judges:
        if not isinstance(judge, dict):
            continue
        name = judge.get("name")
        score_range = judge.get("score_range")
        if not name or not isinstance(score_range, (list, tuple)) or len(score_range) != 2:
            continue
        try:
            lo, hi = float(score_range[0]), float(score_range[1])
        except (TypeError, ValueError):
            continue
        ranges[str(name)] = (lo, hi)
    return ranges


def _normalize_numeric_value(
    value: int | float,
    score_range: tuple[float, float] | None = None,
) -> float:
    """Map a numeric judge onto [0, 1] using the same Likert vs unit scale as above.

    When the judge's declared ``score_range`` is known (see
    ``load_judge_score_ranges``), it is authoritative and removes the
    int/float ambiguity below entirely: a bare ``value: 1`` from a judge
    declared ``score_range: [0, 1]`` is unambiguously the ceiling (1.0), not
    a Likert floor, regardless of whether the judge emitted it as an int or
    a float.

    Without a declared ``score_range``, fall back to guessing from the raw
    value's type/magnitude: integer 1-5 is treated as a Likert score (Harbor
    ``score_range: [1, 5]``): 1 is the floor (reward 0.0), 5 is the ceiling
    (reward 1.0). A unit-scale perfect score must be the float ``1.0``, not
    the integer ``1`` -- YAML ``value: 1`` from ``feedback_type: int`` is the
    worst rubric bucket, not 100%. This heuristic is ambiguous for a bare
    ``1`` and is only used when the caller has no better information.
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if score_range is not None:
        lo, hi = score_range
        if hi > lo:
            return max(0.0, min(1.0, (float(value) - lo) / (hi - lo)))
    if isinstance(value, int) and 1 <= value <= 5:
        return (value - 1) / 4.0
    v = float(value)
    if 0.0 <= v <= 1.0:
        return v
    if 1.0 < v <= 5.0:
        return (v - 1.0) / 4.0
    return v


def _judge_errored(result: Any) -> bool:
    return isinstance(result, dict) and bool(result.get("error")) and result.get("value") is None


def _is_llm_or_numeric_judge(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    jt = result.get("judge_type")
    if jt == "llm":
        return True
    if jt == "check":
        return False
    value = result.get("value")
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def case_reward_from_judges(
    case_data: Any,
    score_ranges: dict[str, tuple[float, float]] | None = None,
) -> float | None:
    """Derive a single grounded reward from one case's judge results.

    This is the canonical reward computation shared by ``the AEH result aggregator``
    (report generation) and the AEH engine's pairwise gate (disagreement
    detection) so the two never silently drift apart, as happened when the
    report's ``trials[]``/``summary`` blocks were computed independently of
    the gate's own grounded per-case view.

    Successful numeric judges are normalized to [0, 1] and averaged. Failed
    boolean judges gate the case to 0.0. Errored judges are skipped -- they are
    not treated as 0.0. If the only remaining signal is booleans but an LLM
    judge errored, return None so operational checks cannot masquerade as a
    quality score.

    ``score_ranges`` (typically from :func:`load_judge_score_ranges`) maps
    judge name -> declared ``(lo, hi)`` scale. When a judge's name is present,
    its value is normalized authoritatively against that declared range
    instead of the ambiguous int/float heuristic in
    :func:`_normalize_numeric_value`. Judges absent from ``score_ranges`` (or
    when ``score_ranges`` itself is omitted) fall back to that heuristic,
    unchanged.

    This is a measure of the case's own correctness (grounded in its own
    judges) -- never a comparison against another run. It is deliberately
    independent of any pairwise win/loss outcome.
    """
    if not isinstance(case_data, dict):
        return None

    if isinstance(case_data.get("reward"), (int, float)) and not isinstance(case_data.get("reward"), bool):
        return float(case_data["reward"])

    numeric: list[float] = []
    bools: list[bool] = []
    scoring_judge_errored = False
    for key, result in case_data.items():
        if key == "reward":
            continue
        if _judge_errored(result):
            if _is_llm_or_numeric_judge(result) or (isinstance(result, dict) and result.get("judge_type") == "llm"):
                scoring_judge_errored = True
            elif isinstance(result, dict) and result.get("judge_type") not in ("check", "builtin"):
                scoring_judge_errored = True
            continue
        if isinstance(result, dict) and "value" in result:
            value = result.get("value")
        else:
            value = result
        if isinstance(value, bool):
            bools.append(value)
        elif isinstance(value, (int, float)):
            score_range = score_ranges.get(key) if score_ranges else None
            numeric.append(_normalize_numeric_value(value, score_range))

    if numeric:
        if bools and not all(bools):
            return 0.0
        return sum(numeric) / len(numeric)
    if bools:
        if scoring_judge_errored:
            return None
        return 1.0 if all(bools) else 0.0
    return None
