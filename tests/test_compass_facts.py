"""Tests for Compass Facts API integration."""

import json
import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from abevalflow.certification import (
    CertificationLevel,
    CertificationResult,
    CheckId,
    CheckResult,
    LevelResult,
)
from abevalflow.compass_facts import (
    FactPushResult,
    UnresolvedEnvVarError,
    _build_certification_level_payload,
    _build_fact_payload,
    _check_unresolved_env_vars,
    _resolve_env_vars,
    push_certification_facts,
    push_gate_fact,
    push_gate_fact_from_config,
    validate_push_facts_config,
)
from abevalflow.gates.base import GateMode, GateResult, GateType
from abevalflow.schemas import PushFactsConfig


@pytest.fixture
def sample_gate_result() -> GateResult:
    """Create a sample gate result for testing."""
    return GateResult(
        gate_type=GateType.ENGINE,
        gate_name="evaluation",
        policy_key="harbor",
        passed=True,
        score=0.85,
        mode=GateMode.BLOCK,
        message="Harbor evaluation passed",
        details={"engine": "harbor", "uplift": 0.15, "treatment_pass_rate": 0.9},
        evaluated_at=datetime(2026, 6, 21, 12, 0, 0, tzinfo=UTC),
    )


@pytest.fixture
def sample_push_facts_config() -> PushFactsConfig:
    """Create a sample push facts configuration."""
    return PushFactsConfig(
        endpoint="https://compass.stage.redhat.com/api/soundcheck/facts/",
        entity_ref="component:default/abevalflow",
        fact_ref_prefix="catalog:default/abevalflow_",
    )


class TestResolveEnvVars:
    """Tests for _resolve_env_vars function."""

    def test_resolves_single_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret123")
        result = _resolve_env_vars("${MY_TOKEN}")
        assert result == "secret123"

    def test_resolves_multiple_env_vars(self, monkeypatch):
        monkeypatch.setenv("HOST", "example.com")
        monkeypatch.setenv("PORT", "8080")
        result = _resolve_env_vars("https://${HOST}:${PORT}/api")
        assert result == "https://example.com:8080/api"

    def test_leaves_unset_vars_unchanged(self):
        result = _resolve_env_vars("${UNSET_VAR_12345}")
        assert result == "${UNSET_VAR_12345}"

    def test_handles_none(self):
        result = _resolve_env_vars(None)
        assert result is None

    def test_handles_empty_string(self):
        result = _resolve_env_vars("")
        assert result == ""

    def test_handles_no_placeholders(self):
        result = _resolve_env_vars("plain-token-value")
        assert result == "plain-token-value"


class TestBuildFactPayload:
    """Tests for _build_fact_payload function."""

    def test_builds_correct_structure(self, sample_gate_result: GateResult):
        payload = _build_fact_payload(
            gate_result=sample_gate_result,
            entity_ref="component:default/test-skill",
            fact_ref="catalog:default/abevalflow_harbor",
        )

        assert "facts" in payload
        assert payload["cache"] is True
        assert len(payload["facts"]) == 1

        fact = payload["facts"][0]
        assert fact["factRef"] == "catalog:default/abevalflow_harbor"
        assert fact["entityRef"] == "component:default/test-skill"
        assert "data" in fact

    def test_data_contains_gate_fields(self, sample_gate_result: GateResult):
        payload = _build_fact_payload(
            gate_result=sample_gate_result,
            entity_ref="component:default/test",
            fact_ref="catalog:default/test_evaluation_harbor",
        )

        data = payload["facts"][0]["data"]
        assert data["gate_name"] == "evaluation"
        assert data["passed"] is True
        assert data["score"] == 0.85
        assert data["mode"] == "block"
        assert data["message"] == "Harbor evaluation passed"
        assert data["details"]["engine"] == "harbor"
        assert "uplift" in data["details"]

    def test_evaluated_at_is_iso_format(self, sample_gate_result: GateResult):
        payload = _build_fact_payload(
            gate_result=sample_gate_result,
            entity_ref="component:default/test",
            fact_ref="catalog:default/test",
        )

        data = payload["facts"][0]["data"]
        assert data["evaluated_at"] == "2026-06-21T12:00:00+00:00"


class TestPushGateFact:
    """Tests for push_gate_fact function."""

    def test_successful_push(self, sample_gate_result: GateResult):
        with patch("abevalflow.compass_facts.urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            result = push_gate_fact(
                gate_result=sample_gate_result,
                endpoint="https://example.com/api/facts/",
                entity_ref="component:default/test",
                fact_ref="catalog:default/test_evaluation_harbor",
            )

            assert result.success is True
            assert result.gate_name == "evaluation"
            assert result.fact_ref == "catalog:default/test_evaluation_harbor"
            assert result.error is None
            mock_urlopen.assert_called_once()

    def test_successful_push_with_bearer_token(self, sample_gate_result: GateResult):
        with patch("abevalflow.compass_facts.urllib.request.urlopen") as mock_urlopen:
            with patch("abevalflow.compass_facts.urllib.request.Request") as mock_request:
                mock_response = MagicMock()
                mock_response.status = 200
                mock_response.__enter__ = MagicMock(return_value=mock_response)
                mock_response.__exit__ = MagicMock(return_value=False)
                mock_urlopen.return_value = mock_response
                mock_request.return_value = MagicMock()

                result = push_gate_fact(
                    gate_result=sample_gate_result,
                    endpoint="https://example.com/api/facts/",
                    entity_ref="component:default/test",
                    fact_ref="catalog:default/test_evaluation_harbor",
                    bearer_token="test-token-12345",
                )

                assert result.success is True
                # Verify Authorization header was included
                call_kwargs = mock_request.call_args
                headers = call_kwargs.kwargs.get("headers", {})
                assert headers.get("Authorization") == "Bearer test-token-12345"

    def test_http_error_returns_failure(self, sample_gate_result: GateResult):
        with patch("abevalflow.compass_facts.urllib.request.urlopen") as mock_urlopen:
            import urllib.error

            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="https://example.com",
                code=500,
                msg="Internal Server Error",
                hdrs={},
                fp=None,
            )

            result = push_gate_fact(
                gate_result=sample_gate_result,
                endpoint="https://example.com/api/facts/",
                entity_ref="component:default/test",
                fact_ref="catalog:default/test_evaluation_harbor",
            )

            assert result.success is False
            assert result.error == "HTTP 500: Internal Server Error"

    def test_url_error_returns_failure(self, sample_gate_result: GateResult):
        with patch("abevalflow.compass_facts.urllib.request.urlopen") as mock_urlopen:
            import urllib.error

            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

            result = push_gate_fact(
                gate_result=sample_gate_result,
                endpoint="https://example.com/api/facts/",
                entity_ref="component:default/test",
                fact_ref="catalog:default/test_evaluation_harbor",
            )

            assert result.success is False
            assert "Connection refused" in result.error


class TestPushGateFactFromConfig:
    """Tests for push_gate_fact_from_config function."""

    def test_builds_fact_ref_from_prefix(
        self,
        sample_gate_result: GateResult,
        sample_push_facts_config: PushFactsConfig,
    ):

        with patch("abevalflow.compass_facts.push_gate_fact") as mock_push:
            mock_push.return_value = FactPushResult(
                gate_name="evaluation",
                fact_ref="catalog:default/abevalflow_evaluation_harbor",
                success=True,
            )

            result = push_gate_fact_from_config(sample_gate_result, sample_push_facts_config)

            mock_push.assert_called_once()
            call_kwargs = mock_push.call_args
            # fact_ref combines category (evaluation) and implementation (harbor)
            assert call_kwargs.kwargs["fact_ref"] == "catalog:default/abevalflow_evaluation_harbor"
            assert result.success is True


class TestValidatePushFactsConfig:
    """Tests for validate_push_facts_config function."""

    def test_no_warning_when_config_is_none(self, caplog):
        with caplog.at_level(logging.WARNING):
            validate_push_facts_config(None, [])

        assert len(caplog.records) == 0

    def test_no_warning_when_gates_have_push_fact(
        self,
        sample_push_facts_config: PushFactsConfig,
        caplog,
    ):
        with caplog.at_level(logging.WARNING):
            validate_push_facts_config(sample_push_facts_config, ["harbor", "cisco"])

        assert len(caplog.records) == 0

    def test_warns_when_endpoint_set_but_no_gates(
        self,
        sample_push_facts_config: PushFactsConfig,
        caplog,
    ):
        with caplog.at_level(logging.WARNING):
            validate_push_facts_config(sample_push_facts_config, [])

        assert len(caplog.records) == 1
        assert "no gates have push_fact=True" in caplog.records[0].message


class TestGatePolicyPushFactMethods:
    """Tests for GatePolicy push_fact methods."""

    def test_should_push_fact_returns_false_when_no_config(self):
        from abevalflow.schemas import GatePolicy

        policy = GatePolicy()
        assert policy.should_push_fact("harbor") is False

    def test_should_push_fact_returns_false_when_gate_not_configured(self, sample_push_facts_config: PushFactsConfig):
        from abevalflow.schemas import GatePolicy, GatePolicyItem

        policy = GatePolicy(
            push_facts=sample_push_facts_config,
            gates={"security": GatePolicyItem(push_fact=True)},
        )
        assert policy.should_push_fact("evaluation") is False

    def test_should_push_fact_returns_true_when_configured(self, sample_push_facts_config: PushFactsConfig):
        from abevalflow.schemas import GatePolicy, GatePolicyItem

        policy = GatePolicy(
            push_facts=sample_push_facts_config,
            gates={"evaluation": GatePolicyItem(push_fact=True)},
        )
        assert policy.should_push_fact("evaluation") is True

    def test_get_gates_with_push_fact(self, sample_push_facts_config: PushFactsConfig):
        from abevalflow.schemas import GatePolicy, GatePolicyItem

        policy = GatePolicy(
            push_facts=sample_push_facts_config,
            gates={
                "evaluation": GatePolicyItem(push_fact=True),
                "security": GatePolicyItem(push_fact=True),
                "quality": GatePolicyItem(push_fact=False),
            },
        )

        gates = policy.get_gates_with_push_fact()
        assert set(gates) == {"evaluation", "security"}


class TestPushFactsConfigSchema:
    """Tests for PushFactsConfig schema."""

    def test_required_fields(self):
        with pytest.raises(Exception):  # Pydantic validation error
            PushFactsConfig()

    def test_valid_config(self):
        config = PushFactsConfig(
            endpoint="https://compass.stage.redhat.com/api/soundcheck/facts/",
            entity_ref="component:default/abevalflow",
        )
        assert config.endpoint == "https://compass.stage.redhat.com/api/soundcheck/facts/"
        assert config.entity_ref == "component:default/abevalflow"
        assert config.fact_ref_prefix == "catalog:default/abevalflow_"  # default
        assert config.bearer_token is None  # default

    def test_custom_prefix(self):
        config = PushFactsConfig(
            endpoint="https://example.com/api/facts/",
            entity_ref="component:default/test",
            fact_ref_prefix="custom:ns/prefix_",
        )
        assert config.fact_ref_prefix == "custom:ns/prefix_"

    def test_bearer_token(self):
        config = PushFactsConfig(
            endpoint="https://compass.stage.redhat.com/api/soundcheck/facts/",
            entity_ref="component:default/abevalflow",
            bearer_token="my-secret-token",
        )
        assert config.bearer_token == "my-secret-token"

    def test_bearer_token_with_env_var_placeholder(self):
        config = PushFactsConfig(
            endpoint="https://compass.stage.redhat.com/api/soundcheck/facts/",
            entity_ref="component:default/abevalflow",
            bearer_token="${COMPASS_API_TOKEN}",
        )
        assert config.bearer_token == "${COMPASS_API_TOKEN}"


class TestCheckUnresolvedEnvVars:
    """Tests for _check_unresolved_env_vars function."""

    def test_no_error_when_fully_resolved(self):
        _check_unresolved_env_vars("resolved-token-value", "bearer_token")

    def test_no_error_when_none(self):
        _check_unresolved_env_vars(None, "bearer_token")

    def test_no_error_when_empty(self):
        _check_unresolved_env_vars("", "bearer_token")

    def test_raises_when_unresolved_var(self):
        with pytest.raises(UnresolvedEnvVarError) as exc_info:
            _check_unresolved_env_vars("${UNSET_VAR}", "bearer_token")
        assert "Unresolved environment variable" in str(exc_info.value)
        assert "bearer_token" in str(exc_info.value)

    def test_raises_when_partial_unresolved(self):
        with pytest.raises(UnresolvedEnvVarError):
            _check_unresolved_env_vars("prefix_${UNSET}_suffix", "some_field")


class TestBuildCertificationLevelPayload:
    """Tests for _build_certification_level_payload function."""

    def test_uses_level_result_passed(self):
        """Verify payload uses level_result.passed directly (hierarchy pre-enforced)."""
        level_result = LevelResult(
            level=CertificationLevel.CERTIFIED,
            passed=True,
            checks=[
                CheckResult(
                    check_id=CheckId.ADVANCED_AGENT_VALIDATION,
                    name="Test Check",
                    passed=True,
                    score=0.9,
                )
            ],
        )
        payload = _build_certification_level_payload(
            level_result=level_result,
            entity_ref="component:default/test",
            fact_ref="catalog:default/test_certified",
        )
        data = payload["facts"][0]["data"]
        assert data["passed"] is True

    def test_failure_reason_when_hierarchy_forced(self):
        """Verify failure_reason is added when all checks pass but level fails (hierarchy)."""
        # This simulates a level that was forced to fail due to hierarchy
        # (all checks passed but level.passed is False)
        level_result = LevelResult(
            level=CertificationLevel.TRUSTED,
            passed=False,  # Forced to fail by hierarchy
            checks=[
                CheckResult(
                    check_id=CheckId.FUNCTIONAL_VALIDATION,
                    name="Functional Validation",
                    passed=True,  # Check itself passed
                    score=0.9,
                )
            ],
        )
        payload = _build_certification_level_payload(
            level_result=level_result,
            entity_ref="component:default/test",
            fact_ref="catalog:default/test_trusted",
        )
        data = payload["facts"][0]["data"]
        assert data["passed"] is False
        assert data.get("failure_reason") == "prerequisite_level_failed"

    def test_no_failure_reason_when_check_failed(self):
        """Verify failure_reason is NOT added when a check actually failed."""
        level_result = LevelResult(
            level=CertificationLevel.FOUNDATIONAL,
            passed=False,  # Failed because a check failed
            checks=[
                CheckResult(
                    check_id=CheckId.VALID_SKILL_STRUCTURE,
                    name="Valid Skill Structure",
                    passed=False,  # Check actually failed
                    score=0.0,
                )
            ],
        )
        payload = _build_certification_level_payload(
            level_result=level_result,
            entity_ref="component:default/test",
            fact_ref="catalog:default/test_foundational",
        )
        data = payload["facts"][0]["data"]
        assert data["passed"] is False
        assert "failure_reason" not in data


class TestPushCertificationFactsHierarchy:
    """Tests for push_certification_facts with pre-enforced hierarchy.

    Note: Hierarchy enforcement now happens in compute_certification(), not in
    push_certification_facts. These tests verify that push_certification_facts
    correctly passes through the pre-enforced .passed values.
    """

    @pytest.fixture
    def mock_urlopen(self):
        """Mock urlopen for successful responses."""
        with patch("abevalflow.compass_facts.urllib.request.urlopen") as mock:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock.return_value = mock_response
            yield mock

    def test_trusted_fails_certified_also_fails(self, mock_urlopen):
        """With pre-enforced hierarchy: if trusted fails, certified also fails."""
        # This is a properly hierarchy-enforced result from compute_certification
        # (trusted failed its own checks, so certified is also marked as failed)
        certification_result = CertificationResult(
            foundational=LevelResult(level=CertificationLevel.FOUNDATIONAL, passed=True),
            trusted=LevelResult(level=CertificationLevel.TRUSTED, passed=False),
            certified=LevelResult(level=CertificationLevel.CERTIFIED, passed=False),  # Pre-enforced
        )
        push_facts_config = PushFactsConfig(
            endpoint="https://example.com/api/facts/",
            entity_ref="component:default/test",
        )

        payloads_sent = []

        def capture_request(req, **kwargs):
            data = json.loads(req.data.decode("utf-8"))
            payloads_sent.append(data)
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            return mock_response

        mock_urlopen.side_effect = capture_request

        results = push_certification_facts(certification_result, push_facts_config)

        assert len(results) == 4
        assert all(r.success for r in results)

        foundational_payload = payloads_sent[0]
        trusted_payload = payloads_sent[1]
        certified_payload = payloads_sent[2]

        assert foundational_payload["facts"][0]["data"]["passed"] is True
        assert trusted_payload["facts"][0]["data"]["passed"] is False
        assert certified_payload["facts"][0]["data"]["passed"] is False

    def test_foundational_fails_all_fail(self, mock_urlopen):
        """With pre-enforced hierarchy: if foundational fails, all levels fail."""
        # This is a properly hierarchy-enforced result from compute_certification
        certification_result = CertificationResult(
            foundational=LevelResult(level=CertificationLevel.FOUNDATIONAL, passed=False),
            trusted=LevelResult(level=CertificationLevel.TRUSTED, passed=False),  # Pre-enforced
            certified=LevelResult(level=CertificationLevel.CERTIFIED, passed=False),  # Pre-enforced
        )
        push_facts_config = PushFactsConfig(
            endpoint="https://example.com/api/facts/",
            entity_ref="component:default/test",
        )

        payloads_sent = []

        def capture_request(req, **kwargs):
            data = json.loads(req.data.decode("utf-8"))
            payloads_sent.append(data)
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            return mock_response

        mock_urlopen.side_effect = capture_request

        push_certification_facts(certification_result, push_facts_config)

        foundational_payload = payloads_sent[0]
        trusted_payload = payloads_sent[1]
        certified_payload = payloads_sent[2]

        assert foundational_payload["facts"][0]["data"]["passed"] is False
        assert trusted_payload["facts"][0]["data"]["passed"] is False
        assert certified_payload["facts"][0]["data"]["passed"] is False

    def test_all_passed_when_hierarchy_complete(self, mock_urlopen):
        """All levels show passed=True when full hierarchy passes."""
        certification_result = CertificationResult(
            foundational=LevelResult(level=CertificationLevel.FOUNDATIONAL, passed=True),
            trusted=LevelResult(level=CertificationLevel.TRUSTED, passed=True),
            certified=LevelResult(level=CertificationLevel.CERTIFIED, passed=True),
        )
        push_facts_config = PushFactsConfig(
            endpoint="https://example.com/api/facts/",
            entity_ref="component:default/test",
        )

        payloads_sent = []

        def capture_request(req, **kwargs):
            data = json.loads(req.data.decode("utf-8"))
            payloads_sent.append(data)
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            return mock_response

        mock_urlopen.side_effect = capture_request

        push_certification_facts(certification_result, push_facts_config)

        for payload in payloads_sent[:3]:
            assert payload["facts"][0]["data"]["passed"] is True

    def test_raises_on_unresolved_bearer_token(self):
        """Should raise UnresolvedEnvVarError if bearer_token is not resolved."""
        certification_result = CertificationResult(
            foundational=LevelResult(level=CertificationLevel.FOUNDATIONAL, passed=True),
            trusted=LevelResult(level=CertificationLevel.TRUSTED, passed=True),
            certified=LevelResult(level=CertificationLevel.CERTIFIED, passed=True),
        )
        push_facts_config = PushFactsConfig(
            endpoint="https://example.com/api/facts/",
            entity_ref="component:default/test",
            bearer_token="${UNSET_TOKEN}",
        )

        with pytest.raises(UnresolvedEnvVarError) as exc_info:
            push_certification_facts(certification_result, push_facts_config)

        assert "bearer_token" in str(exc_info.value)
