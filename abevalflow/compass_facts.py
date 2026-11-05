"""Client for pushing gate results to Red Hat Compass Facts API.

The Compass Soundcheck Facts API accepts evaluation results that can be
displayed in the Compass developer portal alongside other component metrics.

Usage:
    from abevalflow.compass_facts import push_gate_fact
    from abevalflow.gates.base import GateResult

    push_gate_fact(
        gate_result=result,
        endpoint="https://compass.stage.redhat.com/api/soundcheck/facts/",
        entity_ref="component:default/my-skill",
        fact_ref="catalog:default/abevalflow_harbor",
    )
"""

from __future__ import annotations

import json
import logging
import os
import re
import ssl
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from abevalflow.certification import CheckId

if TYPE_CHECKING:
    from abevalflow.certification import CertificationResult, LevelResult
    from abevalflow.gates.base import GateResult
    from abevalflow.schemas import PushFactsConfig

logger = logging.getLogger(__name__)


def _tls_context(verify_tls: bool) -> ssl.SSLContext | None:
    """Use urllib's normal trust store unless this publication opts out."""
    if verify_tls:
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _resolve_env_vars(value: str | None) -> str | None:
    """Resolve ${VAR} placeholders from environment.

    Args:
        value: String that may contain ${VAR} placeholders

    Returns:
        String with placeholders replaced by environment variable values.
        If a variable is not set, the placeholder is left unchanged.
    """
    if not value:
        return value
    pattern = r"\$\{([^}]+)\}"

    def replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, match.group(0))

    return re.sub(pattern, replace, value)


class FactPushResult(BaseModel):
    """Result of a single fact push attempt."""

    gate_name: str = Field(description="Name of the gate whose fact was pushed")
    fact_ref: str = Field(description="Fact reference that was used")
    success: bool = Field(description="Whether the push succeeded")
    error: str | None = Field(default=None, description="Error message if push failed")
    pushed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of the push attempt",
    )


def _build_fact_payload(
    gate_result: GateResult,
    entity_ref: str,
    fact_ref: str,
) -> dict[str, Any]:
    """Build Soundcheck fact payload from gate result.

    Args:
        gate_result: The gate evaluation result
        entity_ref: Compass entity reference (e.g. component:default/abevalflow)
        fact_ref: Unique fact identifier (e.g. catalog:default/abevalflow_harbor)

    Returns:
        Dict ready to be JSON-serialized and POSTed to Facts API
    """
    return {
        "cache": True,
        "facts": [
            {
                "factRef": fact_ref,
                "entityRef": entity_ref,
                "data": {
                    "gate_name": gate_result.gate_name,
                    "passed": gate_result.passed,
                    "score": gate_result.score,
                    "mode": gate_result.mode.value if gate_result.mode else "unknown",
                    "message": gate_result.message or "",
                    "details": gate_result.details or {},
                    "evaluated_at": (
                        gate_result.evaluated_at.isoformat()
                        if gate_result.evaluated_at
                        else datetime.now(UTC).isoformat()
                    ),
                },
            }
        ]
    }


def push_gate_fact(
    gate_result: GateResult,
    endpoint: str,
    entity_ref: str,
    fact_ref: str,
    timeout_sec: float = 30.0,
    bearer_token: str | None = None,
    verify_tls: bool = True,
) -> FactPushResult:
    """Push a gate result to Compass Facts API.

    Args:
        gate_result: The gate evaluation result to push
        endpoint: Compass Facts API URL
        entity_ref: Compass entity reference
        fact_ref: Unique fact identifier for this gate
        timeout_sec: Request timeout in seconds
        bearer_token: Optional bearer token for authentication
        verify_tls: Verify the server certificate and hostname (default: True)

    Returns:
        FactPushResult with success status and any error details
    """
    payload = _build_fact_payload(gate_result, entity_ref, fact_ref)

    try:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"

        request = urllib.request.Request(
            endpoint,
            data=data,
            headers=headers,
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=timeout_sec, context=_tls_context(verify_tls)) as response:
            status = response.status
            if status in (200, 201, 202):
                logger.info(
                    "Pushed fact %s for gate %s (status=%d)",
                    fact_ref,
                    gate_result.gate_name,
                    status,
                )
                return FactPushResult(
                    gate_name=gate_result.gate_name,
                    fact_ref=fact_ref,
                    success=True,
                )
            else:
                error_msg = f"Unexpected status {status}"
                logger.warning(
                    "Unexpected status %d pushing fact %s",
                    status,
                    fact_ref,
                )
                return FactPushResult(
                    gate_name=gate_result.gate_name,
                    fact_ref=fact_ref,
                    success=False,
                    error=error_msg,
                )

    except urllib.error.HTTPError as e:
        error_msg = f"HTTP {e.code}: {e.reason}"
        logger.error(
            "HTTP error pushing fact %s: %d %s",
            fact_ref,
            e.code,
            e.reason,
        )
        return FactPushResult(
            gate_name=gate_result.gate_name,
            fact_ref=fact_ref,
            success=False,
            error=error_msg,
        )
    except urllib.error.URLError as e:
        error_msg = f"URL error: {e.reason}"
        logger.error("URL error pushing fact %s: %s", fact_ref, e.reason)
        return FactPushResult(
            gate_name=gate_result.gate_name,
            fact_ref=fact_ref,
            success=False,
            error=error_msg,
        )
    except Exception as e:
        error_msg = str(e)
        logger.error("Error pushing fact %s: %s", fact_ref, e)
        return FactPushResult(
            gate_name=gate_result.gate_name,
            fact_ref=fact_ref,
            success=False,
            error=error_msg,
        )


def push_gate_fact_from_config(
    gate_result: GateResult,
    push_facts_config: PushFactsConfig,
    *,
    verify_tls: bool = True,
) -> FactPushResult:
    """Push a gate result using PushFactsConfig settings.

    Convenience wrapper that builds the fact_ref from the config prefix,
    category name, and implementation name.

    The fact_ref format is: {prefix}{category}_{implementation}
    For example: catalog:default/abevalflow_evaluation_harbor

    Args:
        gate_result: The gate evaluation result to push
        push_facts_config: Configuration with endpoint, entity_ref, and prefix

    Returns:
        FactPushResult with success status and any error details

    Raises:
        UnresolvedEnvVarError: If bearer_token contains unresolved ${VAR} placeholders
    """
    policy_key = gate_result.get_policy_key()
    if policy_key and policy_key != gate_result.gate_name:
        fact_ref = f"{push_facts_config.fact_ref_prefix}{gate_result.gate_name}_{policy_key}"
    else:
        fact_ref = f"{push_facts_config.fact_ref_prefix}{gate_result.gate_name}"

    resolved_token = _resolve_env_vars(push_facts_config.bearer_token)
    _check_unresolved_env_vars(resolved_token, "bearer_token")

    return push_gate_fact(
        gate_result=gate_result,
        endpoint=push_facts_config.endpoint,
        entity_ref=push_facts_config.entity_ref,
        fact_ref=fact_ref,
        bearer_token=resolved_token,
        verify_tls=verify_tls,
    )


def validate_push_facts_config(
    push_facts_config: PushFactsConfig | None,
    gates_with_push_fact: list[str],
) -> None:
    """Validate push_facts configuration and warn if misconfigured.

    Logs a warning if endpoint is configured but no gates have push_fact=True.

    Args:
        push_facts_config: The push_facts configuration (may be None)
        gates_with_push_fact: List of gate names with push_fact=True
    """
    if push_facts_config is None:
        return

    if not gates_with_push_fact:
        logger.warning(
            "push_facts.endpoint is configured (%s) but no gates have push_fact=True. "
            "No gate facts will be pushed. Certification and skill facts are configured independently.",
            push_facts_config.endpoint,
        )


class CertificationFactPushResult(BaseModel):
    """Result of pushing a certification level, summary, or check fact."""

    level: str = Field(description="Certification level, summary, or standalone check fact name")
    fact_ref: str = Field(description="Fact reference that was used")
    success: bool = Field(description="Whether the push succeeded")
    error: str | None = Field(default=None, description="Error message if push failed")
    pushed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp of the push attempt",
    )


def _build_certification_level_payload(
    level_result: LevelResult,
    entity_ref: str,
    fact_ref: str,
) -> dict[str, Any]:
    """Build Soundcheck fact payload for a certification level.

    Args:
        level_result: The certification level result (with hierarchy already enforced)
        entity_ref: Compass entity reference
        fact_ref: Unique fact identifier

    Returns:
        Dict ready to be JSON-serialized and POSTed to Facts API
    """
    checks_data = []
    for check in level_result.checks:
        checks_data.append(
            {
                "check_id": check.check_id.value,
                "name": check.name,
                "passed": check.passed,
                "score": check.score,
                "message": check.message,
                "source_gate": check.source_gate,
            }
        )

    # Determine if this level failed due to hierarchy (all checks passed but level failed)
    all_checks_passed = level_result.checks_total > 0 and level_result.checks_passed == level_result.checks_total
    hierarchy_forced_failure = not level_result.passed and all_checks_passed

    data: dict[str, Any] = {
        "level": level_result.level.value,
        "passed": level_result.passed,
        "checks_passed": level_result.checks_passed,
        "checks_total": level_result.checks_total,
        "overall_score": level_result.overall_score,
        "checks": checks_data,
        "evaluated_at": datetime.now(UTC).isoformat(),
    }

    if hierarchy_forced_failure:
        data["failure_reason"] = "prerequisite_level_failed"

    return {
        "facts": [
            {
                "factRef": fact_ref,
                "entityRef": entity_ref,
                "data": data,
            }
        ]
    }


def _build_certification_summary_payload(
    certification_result: CertificationResult,
    entity_ref: str,
    fact_ref: str,
) -> dict[str, Any]:
    """Build Soundcheck fact payload for certification summary.

    Args:
        certification_result: Complete certification result (with hierarchy already enforced)
        entity_ref: Compass entity reference
        fact_ref: Unique fact identifier

    Returns:
        Dict ready to be JSON-serialized and POSTed to Facts API

    Note:
        The .passed values on each level already reflect hierarchy enforcement
        from compute_certification(). No additional hierarchy logic needed here.
    """
    return {
        "facts": [
            {
                "factRef": fact_ref,
                "entityRef": entity_ref,
                "data": {
                    "highest_level": certification_result.highest_level.value,
                    "foundational_passed": certification_result.foundational.passed,
                    "foundational_score": certification_result.foundational.overall_score,
                    "trusted_passed": certification_result.trusted.passed,
                    "trusted_score": certification_result.trusted.overall_score,
                    "certified_passed": certification_result.certified.passed,
                    "certified_score": certification_result.certified.overall_score,
                    "evaluated_at": datetime.now(UTC).isoformat(),
                },
            }
        ]
    }


def push_certification_level_fact(
    level_result: LevelResult,
    endpoint: str,
    entity_ref: str,
    fact_ref: str,
    timeout_sec: float = 30.0,
    bearer_token: str | None = None,
    verify_tls: bool = True,
) -> CertificationFactPushResult:
    """Push a certification level result to Compass Facts API.

    Args:
        level_result: The certification level result to push (with hierarchy already enforced)
        endpoint: Compass Facts API URL
        entity_ref: Compass entity reference
        fact_ref: Unique fact identifier for this level
        timeout_sec: Request timeout in seconds
        bearer_token: Optional bearer token for authentication
        verify_tls: Verify the server certificate and hostname (default: True)

    Returns:
        CertificationFactPushResult with success status

    Note:
        The level_result.passed value already reflects hierarchy enforcement
        from compute_certification(). No additional hierarchy logic needed here.
    """
    payload = _build_certification_level_payload(level_result, entity_ref, fact_ref)

    return _push_raw_fact(
        payload=payload,
        endpoint=endpoint,
        fact_ref=fact_ref,
        level=level_result.level.value,
        timeout_sec=timeout_sec,
        bearer_token=bearer_token,
        verify_tls=verify_tls,
    )


class UnresolvedEnvVarError(Exception):
    """Raised when an environment variable placeholder remains unresolved."""

    pass


def _check_unresolved_env_vars(value: str | None, context: str) -> None:
    """Check if a resolved value still contains unresolved ${VAR} placeholders.

    Args:
        value: The resolved value to check
        context: Description of what the value is for (used in error message)

    Raises:
        UnresolvedEnvVarError: If unresolved placeholders are found
    """
    if value and "${" in value:
        logger.error(
            "Unresolved environment variable in %s: value contains '${'. "
            "Ensure the referenced environment variable is set.",
            context,
        )
        raise UnresolvedEnvVarError(
            f"Unresolved environment variable in {context}: "
            f"value still contains '${{'. Check that the environment variable is set."
        )


def _push_raw_fact(
    payload: dict[str, Any],
    endpoint: str,
    fact_ref: str,
    level: str,
    timeout_sec: float = 30.0,
    bearer_token: str | None = None,
    verify_tls: bool = True,
) -> CertificationFactPushResult:
    """Push a raw fact payload to Compass Facts API.

    Args:
        payload: The JSON payload to push
        endpoint: Compass Facts API URL
        fact_ref: Fact reference for logging/result
        level: Level name for the result
        timeout_sec: Request timeout in seconds
        bearer_token: Optional bearer token for authentication
        verify_tls: Verify the server certificate and hostname (default: True)

    Returns:
        CertificationFactPushResult with success status
    """
    try:
        # Retain pushed values for fact inspection and subsequent check execution.
        # Without explicit caching, a successful POST need not make facts retrievable.
        data = json.dumps({**payload, "cache": True}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"

        request = urllib.request.Request(
            endpoint,
            data=data,
            headers=headers,
            method="POST",
        )

        with urllib.request.urlopen(request, timeout=timeout_sec, context=_tls_context(verify_tls)) as response:
            status = response.status
            if status in (200, 201, 202):
                logger.info("Pushed %s fact %s (status=%d)", level, fact_ref, status)
                return CertificationFactPushResult(
                    level=level,
                    fact_ref=fact_ref,
                    success=True,
                )
            else:
                error_msg = f"Unexpected status {status}"
                logger.warning(
                    "Unexpected status %d pushing %s fact %s",
                    status,
                    level,
                    fact_ref,
                )
                return CertificationFactPushResult(
                    level=level,
                    fact_ref=fact_ref,
                    success=False,
                    error=error_msg,
                )

    except urllib.error.HTTPError as e:
        error_msg = f"HTTP {e.code}: {e.reason}"
        logger.error("HTTP error pushing %s fact %s: %d %s", level, fact_ref, e.code, e.reason)
        return CertificationFactPushResult(
            level=level,
            fact_ref=fact_ref,
            success=False,
            error=error_msg,
        )
    except urllib.error.URLError as e:
        error_msg = f"URL error: {e.reason}"
        logger.error("URL error pushing %s fact %s: %s", level, fact_ref, e.reason)
        return CertificationFactPushResult(
            level=level,
            fact_ref=fact_ref,
            success=False,
            error=error_msg,
        )
    except Exception as e:
        error_msg = str(e)
        logger.error("Error pushing %s fact %s: %s", level, fact_ref, e)
        return CertificationFactPushResult(
            level=level,
            fact_ref=fact_ref,
            success=False,
            error=error_msg,
        )


def push_certification_facts(
    certification_result: CertificationResult,
    push_facts_config: PushFactsConfig,
    *,
    verify_tls: bool = True,
    include_structure: bool = True,
) -> list[CertificationFactPushResult]:
    """Push certification levels, summary, and skill structure check to Compass.

    Pushes these facts using the configured fact_ref_prefix:
    - catalog:default/abevalflow_foundational
    - catalog:default/abevalflow_trusted
    - catalog:default/abevalflow_certified
    - catalog:default/abevalflow_certification (summary)
    - catalog:default/abevalflow_valid-skill-structure (when the check is present)

    The standalone structure fact uses the check result independently of level
    pass/fail. Custom policies that omit the check do not publish this fact.

    The `passed` field in each level fact respects the certification hierarchy
    (enforced by compute_certification): a level only shows as passed if all
    lower levels also pass.

    Args:
        certification_result: Complete certification result (with hierarchy enforced)
        push_facts_config: Configuration with endpoint and credentials
        include_structure: Publish the legacy standalone structure fact. The
            workflow disables this when publishing all six explicit skill facts.

    Returns:
        List of push results for each fact

    Raises:
        UnresolvedEnvVarError: If bearer_token contains unresolved ${VAR} placeholders

    Note:
        Hierarchy enforcement happens in compute_certification(), not here.
        The .passed values on each level already reflect hierarchy requirements.
    """
    results: list[CertificationFactPushResult] = []
    resolved_token = _resolve_env_vars(push_facts_config.bearer_token)
    _check_unresolved_env_vars(resolved_token, "bearer_token")

    for level_name, level_result in [
        ("foundational", certification_result.foundational),
        ("trusted", certification_result.trusted),
        ("certified", certification_result.certified),
    ]:
        fact_ref = f"{push_facts_config.fact_ref_prefix}{level_name}"
        result = push_certification_level_fact(
            level_result=level_result,
            endpoint=push_facts_config.endpoint,
            entity_ref=push_facts_config.entity_ref,
            fact_ref=fact_ref,
            bearer_token=resolved_token,
            verify_tls=verify_tls,
        )
        results.append(result)

    summary_fact_ref = f"{push_facts_config.fact_ref_prefix}certification"
    summary_payload = _build_certification_summary_payload(
        certification_result,
        push_facts_config.entity_ref,
        summary_fact_ref,
    )
    results.append(
        _push_raw_fact(
            payload=summary_payload,
            endpoint=push_facts_config.endpoint,
            fact_ref=summary_fact_ref,
            level="summary",
            bearer_token=resolved_token,
            verify_tls=verify_tls,
        )
    )

    structure_check = next(
        (
            check
            for level_result in (
                certification_result.foundational,
                certification_result.trusted,
                certification_result.certified,
            )
            for check in level_result.checks
            if check.check_id == CheckId.VALID_SKILL_STRUCTURE
        ),
        None,
    )
    if include_structure and structure_check is not None:
        fact_name = "valid-skill-structure"
        fact_ref = f"{push_facts_config.fact_ref_prefix}{fact_name}"
        payload = {
            "facts": [
                {
                    "factRef": fact_ref,
                    "entityRef": push_facts_config.entity_ref,
                    "data": {
                        **structure_check.model_dump(mode="json"),
                        "evaluated_at": datetime.now(UTC).isoformat(),
                    },
                }
            ]
        }
        results.append(
            _push_raw_fact(
                payload=payload,
                endpoint=push_facts_config.endpoint,
                fact_ref=fact_ref,
                level=fact_name,
                bearer_token=resolved_token,
                verify_tls=verify_tls,
            )
        )

    return results
