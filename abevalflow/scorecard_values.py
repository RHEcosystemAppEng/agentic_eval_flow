"""Score values shared by database persistence and Compass skill facts."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from abevalflow.gates.base import GateResult
    from abevalflow.results.run import StepResult


def check_score(step: StepResult, gate: GateResult | None) -> float | None:
    """Return exactly the score stored in check_results, including unavailable evidence."""
    if step.status != "completed" or (gate and gate.details.get("scoring_unavailable")):
        return None
    value = gate.score if gate else step.details.get("overall_score")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def score_100(value: float | None) -> float | None:
    """Match latest_skill_scorecards: ROUND(CAST(100 * score AS NUMERIC), 1)."""
    if value is None or isinstance(value, bool) or not math.isfinite(value):
        return None
    if not 0 <= value <= 1:
        raise ValueError("Skill score must be between 0 and 1")
    return float((Decimal(str(value)) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))
