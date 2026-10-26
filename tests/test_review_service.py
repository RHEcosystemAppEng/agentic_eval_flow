"""Explicit workflow review configuration and shared completion behavior."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from abevalflow.config.runtime import resolve_config
from abevalflow.review.service import ReviewService


def test_skill_review_uses_configured_client_without_legacy_defaults(catalog):
    config = resolve_config(
        {
            **catalog,
            "skill_review": True,
            "llm_base_url": "https://gateway.example/v1",
            "llm_api_key": "test-credential",
            "llm_model": "review-model",
        },
        {},
    )
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"recommendation": "pass"})))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )
    with patch("openai.OpenAI", return_value=client):
        assessment = ReviewService(config).review_skill(config.skill)
    assert assessment["recommendation"] == "pass"
    assert assessment["token_usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "model": "review-model"}
    request = client.chat.completions.create.call_args.kwargs
    assert request["model"] == "review-model"
    assert "temperature" not in request
    assert (config.skill / "SKILL.md").read_text() == request["messages"][1]["content"]
