"""Phase 1 license-compliance gate.

Reads license-scan.json produced by scripts/mcp/license_scan.py (a licensee run
compared against the approved-license allow-list) and converts it into a
standardized GateResult. Fails in block mode when the top-level declared license
is missing or not in the allow-list (emitted as a HIGH finding by the scanner).

ADR Phase 1: "License compliance - verify the MCP server's top-level declared
license against a list of approved/supported licenses ... Scoped to the
top-level declared license only; transitive dependency license scanning is out
of scope."
"""

from __future__ import annotations

from pathlib import Path

from abevalflow.gates.base import GateResult
from abevalflow.gates.security.base import SecurityGate
from abevalflow.observability.decorators import timed_gate
from abevalflow.schemas import GatePolicy

# Approved SPDX license identifiers for the top-level declared license.
# Compared case-insensitively (see license_scan.py::evaluate_licenses).
DEFAULT_ALLOWED_LICENSES: tuple[str, ...] = (
    "apache-2.0",
    "mit",
    "bsd-2-clause",
    "bsd-3-clause",
)


class LicenseGate(SecurityGate):
    """Top-level declared-license allow-list gate (licensee-backed)."""

    name = "mcp-license"
    scan_filename = "license-scan.json"

    @timed_gate
    def evaluate(
        self,
        reports_dir: Path,
        policy: GatePolicy,
    ) -> GateResult:
        """Evaluate the license allow-list scan."""
        return self.evaluate_scan_json(reports_dir, policy)
