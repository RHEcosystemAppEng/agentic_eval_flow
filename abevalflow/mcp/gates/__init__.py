"""MCP evaluation gates — read result JSON, decide pass/fail.

All gates for all evaluation types live here. Static evaluation gates subclass
SecurityGate and read normalized scan JSON. Conformance evaluation uses a single
data-driven gate (ConformanceEvalGate) that reads three-state check result JSON.

Orchestrator functions:
- ``run_static_eval(reports_dir, policy)`` — evaluates all static evaluation gates
- ``run_conformance_eval(reports_dir, policy)`` — evaluates all conformance evaluation gates
"""

from __future__ import annotations

from pathlib import Path

from abevalflow.gates.base import GateResult
from abevalflow.gates.security.base import SecurityGate
from abevalflow.mcp.gates.conformance_eval_gate import ConformanceEvalGate
from abevalflow.mcp.gates.static_eval_license_gate import DEFAULT_ALLOWED_LICENSES, LicenseGate
from abevalflow.mcp.gates.static_eval_no_user_code_gate import NoUserCodeGate
from abevalflow.mcp.gates.static_eval_secrets_gate import SecretsGate
from abevalflow.schemas import GatePolicy

# ---- Static Evaluation ------------------------------------------------------

# Ordered list of the static evaluation gate classes, in execution order.
STATIC_EVAL_GATES: tuple[type[SecurityGate], ...] = (
    SecretsGate,
    NoUserCodeGate,
    LicenseGate,
)


def get_static_eval_gates() -> list[SecurityGate]:
    """Instantiate all static evaluation gates, in order."""
    return [gate_cls() for gate_cls in STATIC_EVAL_GATES]


def run_static_eval(reports_dir: Path, policy: GatePolicy) -> list[GateResult]:
    """Evaluate all static evaluation gates against the scan reports.

    Args:
        reports_dir: Path to ``reports/{submission-name}/`` holding the
            normalized scan JSON files written by the static evaluation scanners.
        policy: Gate policy controlling mode (disabled/warn/block) per gate.

    Returns:
        One GateResult per static evaluation gate, in :data:`STATIC_EVAL_GATES` order.
    """
    return [gate.evaluate(reports_dir, policy) for gate in get_static_eval_gates()]


# ---- Conformance Evaluation -------------------------------------------------

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

CONFORMANCE_EVAL_CHECKS: tuple[str, ...] = LIVE_CHECKS + COMPASS_CHECKS


def get_conformance_eval_gates() -> list[ConformanceEvalGate]:
    """Instantiate one gate per conformance evaluation check, in order."""
    return [ConformanceEvalGate(check) for check in CONFORMANCE_EVAL_CHECKS]


def run_conformance_eval(reports_dir: Path, policy: GatePolicy) -> list[GateResult]:
    """Evaluate every conformance evaluation gate against the check result files in ``reports_dir``."""
    return [gate.evaluate(reports_dir, policy) for gate in get_conformance_eval_gates()]


__all__ = [
    "COMPASS_CHECKS",
    "CONFORMANCE_EVAL_CHECKS",
    "ConformanceEvalGate",
    "DEFAULT_ALLOWED_LICENSES",
    "LIVE_CHECKS",
    "LicenseGate",
    "NoUserCodeGate",
    "STATIC_EVAL_GATES",
    "SecretsGate",
    "get_conformance_eval_gates",
    "get_static_eval_gates",
    "run_conformance_eval",
    "run_static_eval",
]
