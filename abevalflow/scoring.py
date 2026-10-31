"""Deterministic score calculations for scanner evidence."""

from __future__ import annotations

from collections import Counter
from typing import Any

SCAN_FINDING_SCORE_METHOD = "severity_penalty_v1"
SCAN_FINDING_PENALTIES = {
    "critical": 1.0,
    "high": 0.30,
    "medium": 0.10,
    "low": 0.02,
    "info": 0.0,
}


def score_scan_findings(
    findings: list[dict[str, Any]], *, evidence_complete: bool
) -> tuple[float | None, dict[str, Any]]:
    """Return a 0–1 scanner score and an auditable calculation breakdown.

    Each finding subtracts a configured severity penalty from 1.0; the result
    is clamped at zero. Incomplete or failed scans have no score, because an
    absent finding set is not evidence of a clean scan.
    """
    counts = Counter(str(item.get("severity", "info")).lower() for item in findings)
    penalty_by_severity = {
        severity: round(counts.get(severity, 0) * penalty, 6) for severity, penalty in SCAN_FINDING_PENALTIES.items()
    }
    total_penalty = sum(penalty_by_severity.values())
    score = max(0.0, min(1.0, 1.0 - total_penalty)) if evidence_complete else None
    breakdown = {
        "method": SCAN_FINDING_SCORE_METHOD,
        "available": evidence_complete,
        "score_range": [0.0, 1.0],
        "penalties_per_finding": SCAN_FINDING_PENALTIES,
        "finding_counts": {key: counts.get(key, 0) for key in SCAN_FINDING_PENALTIES},
        "penalty_by_severity": penalty_by_severity,
        "total_penalty": round(total_penalty, 6),
        "score": round(score, 6) if score is not None else None,
        "formula": "max(0, 1 - sum(count(severity) * penalty(severity)))",
    }
    return score, breakdown
