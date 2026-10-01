"""MCP evaluation Phase 2 - deterministic contract/conformance (live, no LLM).

Phase 2 is black-box tested against the deployed, running MCP server (ADR
Approach 4). It is deterministic and uses NO LLM. Results follow the three-state
model (pass / fail / not_evaluated).

Two sources feed the same set of gates:
* **Live probes** (scripts/mcp/phase2_probe.py) - checks that need a running
  server: protocol compliance, schema conformance, tool annotations, HTTP
  support, rate limiting, response-size limit, mandatory timeouts, OpenAPI
  conformance.
* **Compass-consumed** (scripts/mcp/compass_fetch.py) - the Compass tier-1
  automated facts consumed rather than re-probed: tool-name rules and
  OAuth-matches-catalog.

Like Phase 1, this package is kept isolated from the skill pipeline: the gates
are instantiated only here via ``run_phase2`` and are never registered in the
global security-gate registry.
"""

from __future__ import annotations

from pathlib import Path

from abevalflow.gates.base import GateResult
from abevalflow.mcp.phase2.base import Phase2Gate
from abevalflow.schemas import GatePolicy

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


__all__ = ["COMPASS_CHECKS", "LIVE_CHECKS", "PHASE2_CHECKS", "Phase2Gate", "get_phase2_gates", "run_phase2"]
