"""Configured LLM reviews for skills and AEH submissions."""

from __future__ import annotations

import json

from abevalflow.config.runtime import RuntimeConfig
from abevalflow.llm_client import LLMResult
from abevalflow.review.submission import review_submission


class ReviewService:
    """Use an explicitly configured client for advisory review and token accounting."""

    def __init__(self, config: RuntimeConfig):
        from openai import OpenAI

        if not (config.llm_base_url and config.llm_model and config.llm_api_key):
            raise ValueError("Review requires LLM base URL, model, and API key")
        self.client = OpenAI(
            base_url=config.llm_base_url, api_key=config.llm_api_key.get_secret_value(), timeout=120, max_retries=2
        )
        self.model = config.llm_model

    def complete(self, **kwargs) -> LLMResult:
        response = self.client.chat.completions.create(model=self.model, **kwargs)
        usage = response.usage
        return LLMResult(
            content=response.choices[0].message.content or "",
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            model=self.model,
        )

    def review_submission(self, submission):
        return review_submission(submission, complete=self.complete)

    def review_skill(self, skill):
        result = self.complete(
            messages=[
                {
                    "role": "system",
                    "content": "Review the skill for clarity, completeness, and operational usefulness. "
                    "Return JSON with overall_score (0 to 1), recommendation (pass, warn, fail), "
                    "summary, and dimensions.",
                },
                {"role": "user", "content": (skill / "SKILL.md").read_text()},
            ],
            max_tokens=4096,
        )
        assessment = json.loads(result.content)
        if not isinstance(assessment, dict) or assessment.get("recommendation") not in {"pass", "warn", "fail"}:
            raise ValueError("Review did not return a valid assessment")
        assessment["token_usage"] = {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "model": result.model,
        }
        return assessment
