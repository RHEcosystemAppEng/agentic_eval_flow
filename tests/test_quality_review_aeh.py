"""Tests for AEH-aware quality review detection and advisory paths."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from abevalflow.llm_client import LLMResult
from abevalflow.review.submission import (
    _advisory_aeh_missing_files,
    review_submission,
)


def _write_meta(submission: Path, **extra) -> None:
    data = {
        "name": "aeh-hello",
        "description": "test",
        "persona": "general",
        "version": "0.1.0",
        "cpus": 1,
        "memory_mb": 512,
        **extra,
    }
    (submission / "metadata.yaml").write_text(yaml.dump(data))


class TestAehAdvisoryMissing:
    def test_missing_cases_is_warn_pass(self, tmp_path: Path):
        sub = tmp_path / "sub"
        sub.mkdir()
        _write_meta(sub, eval_engine="aeh")
        (sub / "eval.yaml").write_text("skill: x\n")
        result = _advisory_aeh_missing_files(sub, "aeh-hello")
        assert result is not None
        assert result["passed"] is True
        assert result["recommendation"] == "warn"

    def test_complete_aeh_no_advisory(self, tmp_path: Path):
        sub = tmp_path / "sub"
        sub.mkdir()
        _write_meta(sub, eval_engine="aeh")
        (sub / "eval.yaml").write_text("skill: x\n")
        case = sub / "cases" / "case-001"
        case.mkdir(parents=True)
        (case / "input.yaml").write_text("prompt: hi\n")
        assert _advisory_aeh_missing_files(sub, "aeh-hello") is None


class TestReviewSubmissionAeh:
    def test_aeh_review_uses_aeh_prompt_and_stays_passed(self, tmp_path: Path):
        sub = tmp_path / "sub"
        sub.mkdir()
        _write_meta(sub, eval_engine="aeh")
        (sub / "eval.yaml").write_text("skill: x\njudges: []\n")
        case = sub / "cases" / "case-001"
        case.mkdir(parents=True)
        (case / "input.yaml").write_text("prompt: hi\n")

        fake = {
            "dimensions": {"coherence": {"score": 0.2, "finding": "weak"}},
            "overall_score": 0.2,
            "recommendation": "fail",
            "summary": "bad",
        }
        payload = __import__("json").dumps(fake)
        with patch(
            "abevalflow.review.submission.llm_client.chat_completion_with_usage",
            return_value=LLMResult(
                content=payload,
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                model="test",
            ),
        ):
            assessment = review_submission(sub)
        assert assessment["engine"] == "aeh"
        assert assessment["passed"] is True  # AEH advisory
        assert assessment["recommendation"] == "fail"
