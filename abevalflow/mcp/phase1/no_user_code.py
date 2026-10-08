"""Phase 1 no-user-code-execution gate.

Reads no-user-code-scan.json produced by scripts/mcp/no_user_code_scan.py (a
normalized semgrep run) and converts it into a standardized GateResult. Fails
in block mode when any HIGH/CRITICAL dynamic/arbitrary-execution pattern is
present (e.g. eval/exec, os.system, subprocess shell=True, Function(),
Runtime.exec).

ADR Phase 1: "Does not execute user-provided code - static analysis of the
artifact for dynamic/arbitrary code execution."
"""

from __future__ import annotations

from pathlib import Path

from abevalflow.gates.base import GateResult
from abevalflow.gates.security.base import SecurityGate
from abevalflow.observability.decorators import timed_gate
from abevalflow.schemas import GatePolicy


class NoUserCodeGate(SecurityGate):
    """Static dynamic-execution pattern scan gate (semgrep-backed)."""

    name = "mcp-no-user-code"
    scan_filename = "no-user-code-scan.json"

    @timed_gate
    def evaluate(
        self,
        reports_dir: Path,
        policy: GatePolicy,
    ) -> GateResult:
        """Evaluate the normalized semgrep dynamic-execution scan."""
        return self.evaluate_scan_json(reports_dir, policy)
