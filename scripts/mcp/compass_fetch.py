#!/usr/bin/env python3
"""MCP Phase 2 Compass-consumed checks.

A few Phase 2 checks are already collected by Compass as real automated facts, so
the pipeline consumes them rather than re-probing (avoids duplicating what
Compass does well). This script maps those Compass SoundCheck facts into the same
three-state per-check result files the Phase 2 gates read.

Consumed checks and their source facts:
    tool-name-rules    <- mcp:default/tools $.allToolNamesValid
    oauth-catalog-match<- mcp:default/security $.oauth.enforced
                                              $.entityAuth.authServerMatch
                                              $.scopeMatch.allScopesMatch

Only these two are consumed: they are the Compass tier-1 *automated* facts worth
surfacing in our aggregate. Metadata reads Compass also owns (schema-present,
documentation, data-source approval) are left to Compass to report directly, per
the ADR ("consumed as existing Compass Check Results"); re-gating them here would
just duplicate Compass.

Facts are supplied from a local JSON file. Live SoundCheck retrieval is not wired
yet: the read endpoint and access permissions are still being settled with the
Compass team, so ``--facts-file`` is the only path until then.

Usage:
    python -m scripts.mcp.compass_fetch --facts-file facts.json --reports-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from scripts.mcp._common import make_finding
from scripts.mcp._phase2 import (
    STATUS_FAIL,
    STATUS_NOT_EVALUATED,
    STATUS_PASS,
    CheckOutcome,
    write_check_result,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

FACT_TOOLS = "mcp:default/tools"
FACT_SECURITY = "mcp:default/security"


def _get(facts: dict[str, Any], fact_ref: str, *path: str) -> Any:
    """Walk ``facts[fact_ref]`` down ``path``; return None if any hop is missing."""
    node: Any = facts.get(fact_ref)
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _bool_outcome(check: str, value: Any, ok_reason: str, bad_reason: str) -> CheckOutcome:
    """pass/fail from a boolean fact; not_evaluated when the fact is absent."""
    if value is None:
        return CheckOutcome(check, STATUS_NOT_EVALUATED, "Source Compass fact not present.", source="compass")
    if value is True:
        return CheckOutcome(check, STATUS_PASS, ok_reason, source="compass")
    finding = make_finding(severity="high", message=bad_reason, rule_id=check)
    return CheckOutcome(check, STATUS_FAIL, bad_reason, source="compass", findings=[finding])


def map_facts(facts: dict[str, Any]) -> list[CheckOutcome]:
    """Map Compass facts to the consumed Phase 2 check outcomes."""
    outcomes: list[CheckOutcome] = []

    outcomes.append(
        _bool_outcome(
            "tool-name-rules",
            _get(facts, FACT_TOOLS, "allToolNamesValid"),
            "Compass reports all tool names valid.",
            "Compass reports one or more tool names invalid.",
        )
    )

    # OAuth matches catalog = enforced AND auth-server matches AND scopes match.
    enforced = _get(facts, FACT_SECURITY, "oauth", "enforced")
    server_match = _get(facts, FACT_SECURITY, "entityAuth", "authServerMatch")
    scope_match = _get(facts, FACT_SECURITY, "scopeMatch", "allScopesMatch")
    if enforced is None and server_match is None and scope_match is None:
        outcomes.append(
            CheckOutcome(
                "oauth-catalog-match", STATUS_NOT_EVALUATED, "No Compass OAuth facts present.", source="compass"
            )
        )
    else:
        findings = []
        if enforced is not True:
            findings.append(
                make_finding(severity="high", message="OAuth not enforced per Compass", rule_id="oauth-enforced")
            )
        if server_match is not True:
            findings.append(
                make_finding(
                    severity="high",
                    message="OAuth auth server does not match catalog per Compass",
                    rule_id="oauth-server-match",
                )
            )
        if scope_match is not True:
            findings.append(
                make_finding(
                    severity="high",
                    message="OAuth scopes do not match catalog per Compass",
                    rule_id="oauth-scope-match",
                )
            )
        if findings:
            outcomes.append(
                CheckOutcome(
                    "oauth-catalog-match",
                    STATUS_FAIL,
                    "OAuth configuration does not match the catalog.",
                    source="compass",
                    findings=findings,
                )
            )
        else:
            outcomes.append(
                CheckOutcome(
                    "oauth-catalog-match", STATUS_PASS, "OAuth enforced and matches the catalog.", source="compass"
                )
            )

    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP Phase 2 Compass-consumed checks")
    parser.add_argument(
        "--reports-dir", type=Path, required=True, help="Directory to write <check>-check.json files into"
    )
    parser.add_argument("--facts-file", type=Path, required=True, help="Local JSON file of Compass facts")
    args = parser.parse_args()

    if not args.facts_file.is_file():
        logger.error("Facts file not found: %s", args.facts_file)
        return 1
    facts = json.loads(args.facts_file.read_text())

    outcomes = map_facts(facts)
    for outcome in outcomes:
        write_check_result(args.reports_dir, outcome)
    logger.info("Compass consume complete: %d checks", len(outcomes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
