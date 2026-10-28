"""Publish MCP evaluation results to Compass SoundCheck.

Builds fact payloads from :class:`~abevalflow.mcp.eval_result.McpEvalResult`
and publishes them to a dedicated MCP SoundCheck track via
``POST /api/soundcheck/facts``.

Two Phase 2 checks (tool-name-rules, oauth-catalog-match) are *consumed* from
existing Compass facts and are NOT re-published as individual facts.  They
still appear in the phase summary payload so the track view shows the full
picture.

The consume side lives in :mod:`abevalflow.mcp.checks.conformance_eval_compass_checks`.

Reuses the HTTP POST infrastructure from :mod:`abevalflow.compass_facts`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from abevalflow.compass_facts import (
    CertificationFactPushResult,
    _check_unresolved_env_vars,
    _push_raw_fact,
    _resolve_env_vars,
)
from abevalflow.mcp.phase_config import PHASE_CONFIG, PHASES
from abevalflow.mcp.eval_result import (
    PHASE_CONSUMED_CHECKS,
    McpCheckResult,
    McpEvalResult,
    McpPhaseResult,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FactRef mapping tables
# ---------------------------------------------------------------------------

# Facts are configured per phase; do not add Python conditions for new phases.
PHASE_FACT_MAPS: dict[str, dict[str, str]] = {
    name: {
        check_id: check["fact_ref"]
        for check_id, check in phase["checks"].items()
        if check.get("fact_ref")
    }
    for name, phase in PHASES.items()
}
PHASE_SUMMARY_FACTS: dict[str, str] = {
    name: phase["summary_fact_ref"] for name, phase in PHASES.items()
}
MCP_EVAL_SUMMARY_FACT = PHASE_CONFIG["overall_fact_ref"]

# Backwards compatible aliases (may be imported by existing callers/tests).
PHASE1_FACT_MAP = PHASE_FACT_MAPS.get("static-eval", {})
PHASE2_FACT_MAP = PHASE_FACT_MAPS.get("conformance-eval", {})
PHASE3_FACT_MAP = PHASE_FACT_MAPS.get("behavioral-eval", {})

# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _build_check_fact_payload(
    check: McpCheckResult,
    entity_ref: str,
    fact_ref: str,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "facts": [
            {
                "factRef": fact_ref,
                "entityRef": entity_ref,
                "data": {
                    "passed": check.passed,
                    "score": check.score,
                    "status": check.status,
                    "message": check.message,
                    "source": check.source,
                    "evaluated_at": (evaluated_at or datetime.now(UTC)).isoformat(),
                },
            }
        ]
    }


def _build_phase_summary_payload(
    phase_result: McpPhaseResult,
    entity_ref: str,
    fact_ref: str,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    checks_data = [
        {
            "check_id": c.check_id,
            "name": c.name,
            "passed": c.passed,
            "score": c.score,
            "status": c.status,
            "source": c.source,
        }
        for c in phase_result.checks
    ]
    return {
        "facts": [
            {
                "factRef": fact_ref,
                "entityRef": entity_ref,
                "data": {
                    "phase": phase_result.phase,
                    "passed": phase_result.passed,
                    "checks_passed": phase_result.checks_passed,
                    "checks_evaluated": phase_result.checks_evaluated,
                    "checks_total": phase_result.checks_total,
                    "overall_score": phase_result.overall_score,
                    "checks": checks_data,
                    "evaluated_at": (evaluated_at or datetime.now(UTC)).isoformat(),
                },
            }
        ]
    }


def _build_mcp_summary_payload(
    eval_result: McpEvalResult,
    entity_ref: str,
    fact_ref: str,
    evaluated_at: datetime | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "phases": {
            phase_name: {
                "passed": phase.passed,
                "score": phase.overall_score,
                "checks_evaluated": phase.checks_evaluated,
                "checks_total": phase.checks_total,
            }
            for phase_name, phase in eval_result.phases.items()
        },
        "evaluated_at": (evaluated_at or datetime.now(UTC)).isoformat(),
    }
    return {
        "facts": [
            {
                "factRef": fact_ref,
                "entityRef": entity_ref,
                "data": data,
            }
        ]
    }


# ---------------------------------------------------------------------------
# Push config and orchestrator
# ---------------------------------------------------------------------------


@dataclass
class McpPushConfig:
    """Configuration for publishing MCP eval facts to Compass."""

    endpoint: str
    entity_ref: str
    bearer_token: str | None = None
    dry_run: bool = True


def push_mcp_eval_facts(
    eval_result: McpEvalResult,
    config: McpPushConfig,
) -> list[CertificationFactPushResult]:
    """Publish all MCP evaluation facts to Compass.

    Pushes per-check facts (skipping consumed checks), per-phase summaries,
    and a cross-phase summary.  In dry-run mode, payloads are logged but no
    HTTP calls are made.
    """
    resolved_token = _resolve_env_vars(config.bearer_token)
    _check_unresolved_env_vars(resolved_token, "bearer_token")

    evaluated_at = datetime.now(UTC)
    results: list[CertificationFactPushResult] = []

    for phase_name, phase_result in eval_result.phases.items():
        fact_map = PHASE_FACT_MAPS.get(phase_name, {})
        consumed_checks = PHASE_CONSUMED_CHECKS.get(phase_name, set())
        for check in phase_result.checks:
            if check.status == "not_evaluated":
                logger.info("Skipping not-evaluated check %s (no individual fact published)", check.check_id)
                continue
            if check.check_id in consumed_checks:
                logger.debug("Skipping consumed check %s (not re-published)", check.check_id)
                continue
            fact_ref = fact_map.get(check.check_id)
            if fact_ref is None:
                logger.warning("No factRef mapping for check %s; skipping", check.check_id)
                continue
            payload = _build_check_fact_payload(check, config.entity_ref, fact_ref, evaluated_at)
            results.append(
                _push_or_dry_run(payload, config.endpoint, fact_ref, check.check_id, resolved_token, config.dry_run)
            )

    for phase_key, phase_result in eval_result.phases.items():
        fact_ref = PHASE_SUMMARY_FACTS.get(phase_key)
        if fact_ref is None:
            logger.warning("No phase summary factRef mapping for phase %s; skipping", phase_key)
            continue
        payload = _build_phase_summary_payload(phase_result, config.entity_ref, fact_ref, evaluated_at)
        results.append(_push_or_dry_run(payload, config.endpoint, fact_ref, phase_key, resolved_token, config.dry_run))

    summary_payload = _build_mcp_summary_payload(eval_result, config.entity_ref, MCP_EVAL_SUMMARY_FACT, evaluated_at)
    results.append(
        _push_or_dry_run(
            summary_payload, config.endpoint, MCP_EVAL_SUMMARY_FACT, "mcp_eval", resolved_token, config.dry_run
        )
    )

    succeeded = sum(1 for r in results if r.success)
    logger.info("MCP publish complete: %d/%d facts pushed", succeeded, len(results))
    return results


def _push_or_dry_run(
    payload: dict[str, Any],
    endpoint: str,
    fact_ref: str,
    level: str,
    bearer_token: str | None,
    dry_run: bool,
) -> CertificationFactPushResult:
    if dry_run:
        logger.info("[DRY RUN] Would push fact %s: %s", fact_ref, json.dumps(payload, indent=2))
        return CertificationFactPushResult(level=level, fact_ref=fact_ref, success=True)
    return _push_raw_fact(
        payload=payload,
        endpoint=endpoint,
        fact_ref=fact_ref,
        level=level,
        bearer_token=bearer_token,
    )
