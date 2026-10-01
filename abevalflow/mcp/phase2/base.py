"""Phase 2 gate: turn a three-state check result file into a GateResult.

Unlike the Phase 1 security gates (one class per gate, reusing
``SecurityGate.evaluate_scan_json``), Phase 2 has many small, uniform checks that
differ only by name and by a three-state status, so a single data-driven gate is
used instead of a subclass per check.

Each check result file (written by scripts/mcp/phase2_probe.py or compass_fetch.py)
has the shape::

    {"check": ..., "status": "pass"|"fail"|"not_evaluated", "source": ...,
     "reason": ..., "findings": [ ... ]}

Status handling:
* ``pass``           -> passed, score 1.0
* ``fail``           -> passed only in warn mode; score weighted by findings
* ``not_evaluated``  -> passed, score 1.0 (never penalize; ADR three-state)
* file missing       -> not_evaluated (the check did not run)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from abevalflow.gates.base import Finding, GateMode, GateResult, GateType, Severity
from abevalflow.observability.decorators import timed_gate
from abevalflow.schemas import GatePolicy

logger = logging.getLogger(__name__)

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_NOT_EVALUATED = "not_evaluated"

_SEVERITY_WEIGHT = {
    Severity.CRITICAL: 0.0,
    Severity.HIGH: 0.25,
    Severity.MEDIUM: 0.5,
    Severity.LOW: 0.75,
    Severity.INFO: 0.9,
}


def _weighted_score(findings: list[Finding]) -> float:
    if not findings:
        return 1.0
    return sum(_SEVERITY_WEIGHT[f.severity] for f in findings) / len(findings)


class Phase2Gate:
    """Data-driven gate for one Phase 2 check."""

    def __init__(self, check: str) -> None:
        self.check = check
        self.result_filename = f"{check}-check.json"

    def _result(
        self,
        *,
        passed: bool,
        score: float,
        mode: GateMode,
        status: str,
        reason: str,
        source: str,
        findings: list[Finding],
    ) -> GateResult:
        return GateResult(
            gate_type=GateType.QUALITY,
            gate_name="quality",
            policy_key=self.check,
            passed=passed,
            score=score,
            mode=mode,
            findings=findings,
            details={"check": self.check, "status": status, "source": source, "reason": reason},
            message=f"{self.check}: {status.upper()} - {reason}" if reason else f"{self.check}: {status.upper()}",
        )

    @timed_gate
    def evaluate(self, reports_dir: Path, policy: GatePolicy) -> GateResult:
        gate_policy = policy.get_gate_policy("quality")
        mode = gate_policy.mode
        path = reports_dir / self.result_filename

        if not path.is_file():
            return self._result(
                passed=True,
                score=1.0,
                mode=mode,
                status=STATUS_NOT_EVALUATED,
                reason=f"{self.result_filename} not found (check did not run)",
                source="none",
                findings=[],
            )

        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            # A corrupt result file is a real pipeline defect, so log it loudly and
            # score 0.0 - but per the three-state model a "couldn't read" must not
            # block outside block mode (mirrors the fail branch below).
            logger.error("Failed to read %s: %s", path, exc)
            return self._result(
                passed=mode != GateMode.BLOCK,
                score=0.0,
                mode=mode,
                status=STATUS_FAIL,
                reason=f"could not parse {self.result_filename}: {exc}",
                source="none",
                findings=[],
            )

        status = data.get("status", STATUS_NOT_EVALUATED)
        reason = data.get("reason", "")
        source = data.get("source", "probe")
        findings = [
            Finding(
                severity=_coerce_severity(f.get("severity")),
                message=f.get("message", ""),
                location=f.get("file_path") or f.get("location"),
                rule_id=f.get("rule_id", "unknown"),
                details=f,
            )
            for f in data.get("findings", [])
        ]

        if status == STATUS_PASS:
            return self._result(
                passed=True, score=1.0, mode=mode, status=status, reason=reason, source=source, findings=findings
            )
        if status == STATUS_NOT_EVALUATED:
            return self._result(
                passed=True, score=1.0, mode=mode, status=status, reason=reason, source=source, findings=findings
            )
        # fail
        passed = mode != GateMode.BLOCK
        # A fail with no findings must not report a perfect score: _weighted_score
        # returns 1.0 for an empty list (correct only on the pass path), so a bare
        # fail is floored to 0.0 here.
        score = _weighted_score(findings) if findings else 0.0
        return self._result(
            passed=passed,
            score=score,
            mode=mode,
            status=STATUS_FAIL,
            reason=reason,
            source=source,
            findings=findings,
        )


def _coerce_severity(value: object) -> Severity:
    try:
        return Severity(str(value).lower().strip())
    except ValueError:
        return Severity.INFO
