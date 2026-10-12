"""Phase 2 Compass-consumed check mapping.

A few Phase 2 checks are already collected by Compass as real automated facts, so
the pipeline consumes them rather than re-probing (avoids duplicating what
Compass does well). This module maps those Compass SoundCheck facts into the same
three-state ``CheckOutcome`` the Phase 2 gates read.

Consumed checks and their source facts::

    tool-name-rules     <- mcp:default/tools $.allToolNamesValid
    oauth-catalog-match <- mcp:default/security $.oauth.enforced
                                                $.entityAuth.authServerMatch
                                                $.scopeMatch.allScopesMatch

Only these two are consumed: the Compass tier-1 *automated* facts worth surfacing
in our aggregate. Metadata Compass owns directly (schema-present, documentation,
data-source approval) is not re-gated here, per the ADR ("consumed as existing
Compass Check Results").

Two consumption modes:

- **File mode** (``map_facts``): reads raw Compass facts from a local JSON file
  and maps subfields to check outcomes. Used for offline/demo runs.
- **Live mode** (``map_results``): maps SoundCheck API check-result states
  (passed/failed) directly to check outcomes. Used when the pipeline fetches
  results from ``GET /api/soundcheck/results``.

The CLI entry point that reads facts from a file or fetches live results from
the SoundCheck API lives in ``scripts/mcp/run_compass_fetch.py``.
"""

from __future__ import annotations

from typing import Any

from abevalflow.mcp.types import (
    STATUS_FAIL,
    STATUS_NOT_EVALUATED,
    STATUS_PASS,
    CheckOutcome,
    make_finding,
)

FACT_TOOLS = "mcp:default/tools"
FACT_SECURITY = "mcp:default/security"

# Compass SoundCheck check IDs on the mcp-scorecard track (tier-1 auto-collected).
CHECK_TOOL_NAME = "mcp-tool-schema-check"
CHECK_OAUTH = "mcp-oauth-scope-match"

# Maps a SoundCheck check ID to the Phase 2 check name our gates read.
_CHECK_ID_TO_PHASE2 = {
    CHECK_TOOL_NAME: "tool-name-rules",
    CHECK_OAUTH: "oauth-catalog-match",
}


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
    # Distinguish an explicit False (a real violation) from an absent fact (None):
    # a missing subfield must NOT be read as a violation, or a partially-reported
    # entity would falsely FAIL and block the phase. Only when every subfield is
    # present and True can we assert the conjunction; any absent subfield with no
    # explicit False leaves us unable to evaluate.
    oauth_fields = [
        ("oauth-enforced", _get(facts, FACT_SECURITY, "oauth", "enforced"), "OAuth not enforced per Compass"),
        (
            "oauth-server-match",
            _get(facts, FACT_SECURITY, "entityAuth", "authServerMatch"),
            "OAuth auth server does not match catalog per Compass",
        ),
        (
            "oauth-scope-match",
            _get(facts, FACT_SECURITY, "scopeMatch", "allScopesMatch"),
            "OAuth scopes do not match catalog per Compass",
        ),
    ]
    violations = [
        make_finding(severity="high", message=msg, rule_id=rule) for rule, value, msg in oauth_fields if value is False
    ]
    missing = [rule for rule, value, _ in oauth_fields if value is None]
    if violations:
        outcomes.append(
            CheckOutcome(
                "oauth-catalog-match",
                STATUS_FAIL,
                "OAuth configuration does not match the catalog.",
                source="compass",
                findings=violations,
            )
        )
    elif not missing:
        outcomes.append(
            CheckOutcome(
                "oauth-catalog-match", STATUS_PASS, "OAuth enforced and matches the catalog.", source="compass"
            )
        )
    elif len(missing) == len(oauth_fields):
        outcomes.append(
            CheckOutcome(
                "oauth-catalog-match", STATUS_NOT_EVALUATED, "No Compass OAuth facts present.", source="compass"
            )
        )
    else:
        outcomes.append(
            CheckOutcome(
                "oauth-catalog-match",
                STATUS_NOT_EVALUATED,
                f"Incomplete Compass OAuth facts (missing: {', '.join(missing)}); cannot assert OAuth-matches-catalog.",
                source="compass",
            )
        )

    return outcomes


# ---------------------------------------------------------------------------
# Live mode: map SoundCheck API check results
# ---------------------------------------------------------------------------

# TODO: fallback opportunity — for tool-name-rules, we already have the tool
# list from tools/list and the regex is ^[A-Za-z0-9_\-.]{1,128}$. If Compass
# returns no result (new server not yet onboarded), we could validate names
# directly instead of reporting not_evaluated. OAuth cannot fall back (needs
# catalog + PRM data we don't have). Not implemented: ADR says "reuse Compass
# result", so consume-only for now.


def map_results(results: list[dict]) -> list[CheckOutcome]:
    """Map SoundCheck API check results to Phase 2 check outcomes.

    ``results`` is the ``results`` array from
    ``GET /api/soundcheck/results?entityRef=...&checks=...``.  Each element has
    at least ``checkId`` and ``state`` (passed/failed/error/not-applicable).

    Any consumed check not present in the results list is reported
    ``not_evaluated`` (the entity may not be registered in Compass yet).
    """
    by_check_id = {r["checkId"]: r for r in results if isinstance(r, dict) and "checkId" in r}
    outcomes: list[CheckOutcome] = []

    for check_id, phase2_name in _CHECK_ID_TO_PHASE2.items():
        result = by_check_id.get(check_id)
        if result is None:
            outcomes.append(
                CheckOutcome(
                    phase2_name,
                    STATUS_NOT_EVALUATED,
                    f"No Compass result for {check_id}; entity may not be registered yet.",
                    source="compass",
                )
            )
            continue

        state = (result.get("state") or "").lower()
        notes = ""
        details = result.get("details")
        if isinstance(details, dict):
            notes_obj = details.get("notes")
            if isinstance(notes_obj, dict):
                notes = notes_obj.get("data", "")
            elif isinstance(notes_obj, str):
                notes = notes_obj

        if state == "passed":
            outcomes.append(
                CheckOutcome(phase2_name, STATUS_PASS, notes or f"Compass check {check_id} passed.", source="compass")
            )
        elif state == "failed":
            outcomes.append(
                CheckOutcome(
                    phase2_name,
                    STATUS_FAIL,
                    notes or f"Compass check {check_id} failed.",
                    source="compass",
                    findings=[
                        make_finding(severity="high", message=notes or f"{check_id} failed", rule_id=phase2_name)
                    ],
                )
            )
        else:
            # "error", "not-applicable", or unknown state
            outcomes.append(
                CheckOutcome(
                    phase2_name,
                    STATUS_NOT_EVALUATED,
                    f"Compass check {check_id} returned state '{state}': {notes}".strip(),
                    source="compass",
                )
            )

    return outcomes
