"""Phase 1 secrets-management gate.

Reads secrets-scan.json produced by scripts/mcp/secrets_scan.py (a normalized
gitleaks run) and converts it into a standardized GateResult. Fails in block
mode when any HIGH/CRITICAL hardcoded-credential finding is present.

ADR Phase 1: "Secrets management - static scan of the artifact for hardcoded
credentials."
"""

from __future__ import annotations

from pathlib import Path

from abevalflow.gates.base import GateResult
from abevalflow.gates.security.base import SecurityGate
from abevalflow.observability.decorators import timed_gate
from abevalflow.schemas import GatePolicy


class SecretsGate(SecurityGate):
    """Static hardcoded-credential scan gate (gitleaks-backed)."""

    name = "mcp-secrets"
    scan_filename = "secrets-scan.json"

    @timed_gate
    def evaluate(
        self,
        reports_dir: Path,
        policy: GatePolicy,
    ) -> GateResult:
        """Evaluate the normalized gitleaks secrets scan."""
        return self.evaluate_scan_json(reports_dir, policy)
