"""MCP Phase 1 - static / build-time analysis.

Runs three static checks against the MCP server's built artifact/source, before
any deployment and without running the server (ADR Approach 4, Phase 1):

- Secrets management (SecretsGate)
- No user-provided code execution (NoUserCodeGate)
- License compliance (LicenseGate)

Each gate subclasses SecurityGate and reads a normalized ``{"findings": [...]}``
scan JSON emitted by the corresponding scanner in ``scripts/mcp/``. These gates
are instantiated directly from :data:`PHASE1_GATES` via :func:`run_phase1` and
never through the skill pipeline's global security-gate registry
(``abevalflow.gates.security``'s ``_SECURITY_GATE_REGISTRY``), so a Phase 1 run
never executes the skill scanners (CiscoGate, SkillMdScannerGate) and vice versa.
(Note: importing ``abevalflow.gates.security`` still eagerly imports those skill
gate classes - the isolation is that they are never *run* for an MCP server, not
that their modules are never imported.)

Phase 1 always executes, independent of runtime outcomes, so its checks resolve
to pass/fail only (never the not-evaluated state used for Phase 2/3).
"""

from __future__ import annotations

from pathlib import Path

from abevalflow.gates.base import GateResult
from abevalflow.gates.security.base import SecurityGate
from abevalflow.mcp.phase1.license_gate import DEFAULT_ALLOWED_LICENSES, LicenseGate
from abevalflow.mcp.phase1.no_user_code_gate import NoUserCodeGate
from abevalflow.mcp.phase1.secrets_gate import SecretsGate
from abevalflow.schemas import GatePolicy

# Ordered list of the Phase 1 gate classes, in execution order.
PHASE1_GATES: tuple[type[SecurityGate], ...] = (
    SecretsGate,
    NoUserCodeGate,
    LicenseGate,
)


def get_phase1_gates() -> list[SecurityGate]:
    """Instantiate all Phase 1 gates, in order."""
    return [gate_cls() for gate_cls in PHASE1_GATES]


def run_phase1(reports_dir: Path, policy: GatePolicy) -> list[GateResult]:
    """Evaluate all Phase 1 gates against the scan reports.

    Args:
        reports_dir: Path to ``reports/{submission-name}/`` holding the
            normalized scan JSON files written by ``scripts/mcp/*``.
        policy: Gate policy controlling mode (disabled/warn/block) per gate.

    Returns:
        One GateResult per Phase 1 gate, in :data:`PHASE1_GATES` order.
    """
    return [gate.evaluate(reports_dir, policy) for gate in get_phase1_gates()]


__all__ = [
    "DEFAULT_ALLOWED_LICENSES",
    "PHASE1_GATES",
    "LicenseGate",
    "NoUserCodeGate",
    "SecretsGate",
    "get_phase1_gates",
    "run_phase1",
]
