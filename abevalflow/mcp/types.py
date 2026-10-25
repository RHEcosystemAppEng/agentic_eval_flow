"""Core types for the MCP evaluation pipeline.

Defines the data shapes all evaluations use:

- **Conformance evaluation** check outcomes: ``CheckOutcome`` carries a three-state result
  (pass / fail / not_evaluated) that the conformance evaluation gate reads.

Conformance evaluation uses the three-state model because a check that cannot produce a
meaningful signal (server unreachable, no OpenAPI contract, a capability the
server does not expose) must report ``not_evaluated`` rather than a false pass
or fail (ADR: three-state reporting).

Helper functions (``make_finding``, ``write_check_result``) live in
``abevalflow.mcp.helpers``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Conformance evaluation three-state model
# ---------------------------------------------------------------------------

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_NOT_EVALUATED = "not_evaluated"
VALID_STATUSES = (STATUS_PASS, STATUS_FAIL, STATUS_NOT_EVALUATED)


@dataclass
class CheckOutcome:
    """Result of a single conformance evaluation check."""

    check: str
    status: str
    reason: str = ""
    source: str = "probe"
    findings: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"invalid status {self.status!r}; expected one of {VALID_STATUSES}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "check": self.check,
            "status": self.status,
            "source": self.source,
            "reason": self.reason,
            "findings": self.findings,
        }
