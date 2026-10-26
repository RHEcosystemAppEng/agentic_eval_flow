"""LLM-based quality review gate."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from abevalflow.gates.base import Finding, GateMode, GateResult, GateType, Severity
from abevalflow.observability.decorators import timed_gate
from abevalflow.schemas import GatePolicy

logger = logging.getLogger(__name__)


class LLMReviewGate:
    """LLM-based quality review gate.

    Reads _ai_review.json produced by the submission review phase.
    Evaluates coherence, coverage, clarity, feasibility, and robustness.
    """

    name = "llm-review"

    @timed_gate
    def evaluate(
        self,
        workspace_root: Path,
        policy: GatePolicy,
    ) -> GateResult:
        """Evaluate LLM quality review results.

        Mode behavior:
            - DISABLED: Gate is skipped, returns passed=True.
            - WARN: Gate passes unless upstream recommendation is "fail".
            - BLOCK: Gate passes only if overall_score >= threshold.
              In BLOCK mode, the numeric threshold is authoritative — even if
              the LLM reviewer returns recommendation="fail", the gate can
              still pass if the score meets the threshold. This allows
              operators to enforce a strict numeric standard.

        Missing artifact behavior:
            - WARN mode: Returns passed=True (review may have been skipped).
            - BLOCK mode: Returns passed=False (artifact required).
        """
        gate_policy = policy.get_gate_policy("quality")
        threshold = gate_policy.threshold if gate_policy.threshold is not None else 0.6

        if gate_policy.mode == GateMode.DISABLED:
            return GateResult(
                gate_type=GateType.QUALITY,
                gate_name="quality",
                policy_key=self.name,
                passed=True,
                score=1.0,
                mode=GateMode.DISABLED,
                findings=[],
                details={"reviewer": self.name},
                message="LLM quality review disabled",
            )

        review_path = workspace_root / "_ai_review.json"
        if not review_path.exists():
            # In BLOCK mode, missing artifacts fail closed for quality
            if gate_policy.mode == GateMode.BLOCK:
                return GateResult(
                    gate_type=GateType.QUALITY,
                    gate_name="quality",
                    policy_key=self.name,
                    passed=False,
                    score=0.0,
                    mode=gate_policy.mode,
                    findings=[],
                    details={"reviewer": self.name, "review_path": str(review_path), "status": "not_found"},
                    message="FAIL: _ai_review.json missing (required in block mode)",
                )
            # In WARN mode, missing artifacts pass (review may have been skipped)
            return GateResult(
                gate_type=GateType.QUALITY,
                gate_name="quality",
                policy_key=self.name,
                passed=True,
                score=1.0,
                mode=gate_policy.mode,
                findings=[],
                details={"reviewer": self.name, "review_path": str(review_path), "status": "not_found"},
                message="No _ai_review.json found (review may have been skipped)",
            )

        try:
            review_data = json.loads(review_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read quality review: %s", e)
            return GateResult(
                gate_type=GateType.QUALITY,
                gate_name="quality",
                policy_key=self.name,
                passed=False,
                score=0.0,
                mode=gate_policy.mode,
                findings=[],
                details={"reviewer": self.name, "error": str(e)},
                message=f"Failed to parse _ai_review.json: {e}",
            )

        overall_score = review_data.get("overall_score", 0.0)
        recommendation = review_data.get("recommendation", "fail")
        dimensions = review_data.get("dimensions", {})

        findings = []
        low_scores = []

        for dim_name, dim_data in dimensions.items():
            dim_score = dim_data.get("score", 0.0)
            dim_finding = dim_data.get("finding", "")

            if dim_score < 0.4:
                severity = Severity.HIGH
                low_scores.append(dim_name)
            elif dim_score < 0.6:
                severity = Severity.MEDIUM
            elif dim_score < 0.8:
                severity = Severity.LOW
            else:
                severity = Severity.INFO

            if dim_score < 0.6 and dim_finding:
                findings.append(
                    Finding(
                        severity=severity,
                        message=f"{dim_name}: {dim_finding}",
                        location=dim_name,
                        rule_id=f"quality-{dim_name}",
                        details={"score": dim_score},
                    )
                )

        # In BLOCK mode, threshold is authoritative for pass/fail
        # In WARN mode, only hard "fail" recommendations fail the gate
        if gate_policy.mode == GateMode.BLOCK:
            passed = overall_score >= threshold
        else:
            # WARN mode: pass unless explicit "fail" recommendation
            passed = recommendation != "fail"

        summary = review_data.get("summary", "")
        dimension_scores = {name: data.get("score", 0.0) for name, data in dimensions.items()}

        comparison = ">=" if passed else "<"
        message = (
            f"LLM review: score={overall_score:.2f} {comparison} threshold={threshold:.2f} -> "
            f"{'PASS' if passed else 'FAIL'} (upstream: {recommendation})"
        )
        if low_scores:
            message += f" (low: {', '.join(low_scores)})"

        return GateResult(
            gate_type=GateType.QUALITY,
            gate_name="quality",
            policy_key=self.name,
            passed=passed,
            score=overall_score,
            mode=gate_policy.mode,
            threshold=threshold,
            findings=findings,
            details={
                "reviewer": self.name,
                "dimensions": dimension_scores,
                "recommendation": recommendation,
                "summary": summary,
                "review_path": str(review_path),
            },
            message=message,
        )
