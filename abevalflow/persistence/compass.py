"""Explicit publication of computed scorecards to Compass."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from abevalflow.compass_facts import (
    FactPushResult,
    UnresolvedEnvVarError,
    _check_unresolved_env_vars,
    _push_raw_fact,
    _resolve_env_vars,
    push_certification_facts,
    push_gate_fact_from_config,
)
from abevalflow.gates.base import GateResult
from abevalflow.schemas import GatePolicy
from abevalflow.scorecard import Scorecard
from abevalflow.skill_facts import build_skill_facts

if TYPE_CHECKING:
    from abevalflow.report import AnalysisResult
    from abevalflow.results.run import RunResult

logger = logging.getLogger(__name__)


def _maybe_push_fact(gate_result: GateResult, policy: GatePolicy, *, verify_tls: bool = True) -> FactPushResult | None:
    """Push gate fact to Compass if configured.

    Args:
        gate_result: The gate result to potentially push
        policy: Policy containing push_facts configuration

    Returns:
        FactPushResult if push was attempted, None if skipped
    """

    if not policy.should_push_fact(gate_result.gate_name):
        return None

    if policy.push_facts is None:
        return None

    policy_key = gate_result.get_policy_key()
    if policy_key and policy_key != gate_result.gate_name:
        intended_fact_ref = f"{policy.push_facts.fact_ref_prefix}{gate_result.gate_name}_{policy_key}"
    else:
        intended_fact_ref = f"{policy.push_facts.fact_ref_prefix}{gate_result.gate_name}"

    try:
        result = push_gate_fact_from_config(gate_result, policy.push_facts, verify_tls=verify_tls)
        if result.success:
            logger.info("Pushed fact for gate %s to Compass", gate_result.gate_name)
        else:
            logger.warning(
                "Failed to push fact for gate %s: %s",
                gate_result.gate_name,
                result.error,
            )
        return result
    except UnresolvedEnvVarError as e:
        logger.error(
            "Cannot push fact for gate %s: unresolved env var - %s",
            gate_result.gate_name,
            e,
        )
        return FactPushResult(
            gate_name=gate_result.gate_name,
            fact_ref=intended_fact_ref,
            success=False,
            error=str(e),
        )


def publish_scorecard(
    scorecard: Scorecard,
    *,
    verify_tls: bool = True,
    result: RunResult | None = None,
    report: AnalysisResult | None = None,
) -> bool:
    """Publish enabled gate and certification facts without changing the evaluation verdict."""
    from abevalflow.compass_facts import validate_push_facts_config

    validate_push_facts_config(scorecard.policy.push_facts, scorecard.policy.get_gates_with_push_fact())
    if scorecard.policy.push_facts is None:
        return True
    facts = build_skill_facts(scorecard, result, report) if result is not None else []
    if not verify_tls:
        logger.warning("Compass publication: TLS certificate and hostname verification disabled by runtime option")
    scorecard.fact_push_results = [
        pushed
        for gate in scorecard.gates
        if (pushed := _maybe_push_fact(gate, scorecard.policy, verify_tls=verify_tls)) is not None
    ]
    if scorecard.certification:
        scorecard.certification_fact_push_results = push_certification_facts(
            scorecard.certification,
            scorecard.policy.push_facts,
            verify_tls=verify_tls,
            include_structure=result is None,
        )
    if result is not None:
        config = scorecard.policy.push_facts
        token = _resolve_env_vars(config.bearer_token)
        _check_unresolved_env_vars(token, "bearer_token")
        scorecard.skill_fact_push_results = [
            _push_raw_fact(
                payload={"facts": [fact]},
                endpoint=config.endpoint,
                fact_ref=fact["factRef"],
                level=fact["factRef"].removeprefix(config.fact_ref_prefix),
                bearer_token=token,
                verify_tls=verify_tls,
            )
            for fact in facts
        ]
    return all(
        r.success
        for r in [
            *scorecard.fact_push_results,
            *scorecard.certification_fact_push_results,
            *scorecard.skill_fact_push_results,
        ]
    )
