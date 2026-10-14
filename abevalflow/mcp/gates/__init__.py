"""MCP evaluation gates — read result JSON, decide pass/fail.

All gates for both phases live here. Phase 1 gates subclass SecurityGate and
read normalized scan JSON. Phase 2 uses a single data-driven gate (Phase2Gate)
that reads three-state check result JSON.

Orchestrator functions:
- ``run_phase1(reports_dir, policy)`` — evaluates all Phase 1 gates
- ``run_phase2(reports_dir, policy)`` — evaluates all Phase 2 gates
"""

from __future__ import annotations

from pathlib import Path

from abevalflow.gates.base import GateResult
from abevalflow.gates.security.base import SecurityGate
from abevalflow.mcp.gates.phase1_license_gate import DEFAULT_ALLOWED_LICENSES, LicenseGate
from abevalflow.mcp.gates.phase1_no_user_code_gate import NoUserCodeGate
from abevalflow.mcp.gates.phase1_secrets_gate import SecretsGate
from abevalflow.mcp.gates.phase2_conformance_gate import Phase2Gate
from abevalflow.schemas import GatePolicy

# ---- Phase 1 ---------------------------------------------------------------

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
            normalized scan JSON files written by the Phase 1 scanners.
        policy: Gate policy controlling mode (disabled/warn/block) per gate.

    Returns:
        One GateResult per Phase 1 gate, in :data:`PHASE1_GATES` order.
    """
    return [gate.evaluate(reports_dir, policy) for gate in get_phase1_gates()]


# ---- Phase 2 ---------------------------------------------------------------

# Live probe checks (need a running server).
LIVE_CHECKS: tuple[str, ...] = (
    "http-support",
    "protocol-compliance",
    "schema-conformance",
    "tool-annotations",
    "rate-limiting",
    "response-size-limit",
    "mandatory-timeouts",
    "openapi-conformance",
)

# Checks consumed from existing Compass facts (not re-probed).
COMPASS_CHECKS: tuple[str, ...] = (
    "tool-name-rules",
    "oauth-catalog-match",
)

PHASE2_CHECKS: tuple[str, ...] = LIVE_CHECKS + COMPASS_CHECKS


def get_phase2_gates() -> list[Phase2Gate]:
    """Instantiate one gate per Phase 2 check, in order."""
    return [Phase2Gate(check) for check in PHASE2_CHECKS]


def run_phase2(reports_dir: Path, policy: GatePolicy) -> list[GateResult]:
    """Evaluate every Phase 2 gate against the check result files in ``reports_dir``."""
    return [gate.evaluate(reports_dir, policy) for gate in get_phase2_gates()]


__all__ = [
    "COMPASS_CHECKS",
    "DEFAULT_ALLOWED_LICENSES",
    "LIVE_CHECKS",
    "LicenseGate",
    "NoUserCodeGate",
    "PHASE1_GATES",
    "PHASE2_CHECKS",
    "Phase2Gate",
    "SecretsGate",
    "get_phase1_gates",
    "get_phase2_gates",
    "run_phase1",
    "run_phase2",
]
