"""Tests for the quality-review metrics checkpoint command."""

from __future__ import annotations

import json

from abevalflow.cli.compat import write_metrics_checkpoint
from abevalflow.observability.context import MetricsContext


def _run(monkeypatch, *args: str) -> None:
    monkeypatch.setattr("sys.argv", ["write_metrics_checkpoint.py", *args])
    write_metrics_checkpoint.main()


def test_writes_checkpoint_from_review_token_usage(tmp_path, monkeypatch):
    review = tmp_path / "_ai_review.json"
    review.write_text(json.dumps({"token_usage": {"prompt_tokens": 123, "completion_tokens": 45, "model": "judge-v1"}}))
    reports = tmp_path / "reports"

    _run(
        monkeypatch,
        "--run-id",
        "run-1",
        "--submission-name",
        "demo",
        "--report-dir",
        str(reports),
        "--review-file",
        str(review),
    )

    checkpoint = MetricsContext.load_checkpoint(reports)
    assert checkpoint is not None
    assert checkpoint.run_id == "run-1"
    assert checkpoint.submission_name == "demo"
    assert checkpoint.total_prompt_tokens == 123
    assert checkpoint.total_completion_tokens == 45
    assert checkpoint.model_name == "judge-v1"


def test_skips_checkpoint_when_review_has_no_tokens(tmp_path, monkeypatch):
    review = tmp_path / "_ai_review.json"
    review.write_text(json.dumps({"decision": "pass"}))
    reports = tmp_path / "reports"

    _run(
        monkeypatch,
        "--run-id",
        "run-1",
        "--submission-name",
        "demo",
        "--report-dir",
        str(reports),
        "--review-file",
        str(review),
    )

    assert not reports.exists()


def test_malformed_review_is_nonfatal_and_skips_checkpoint(tmp_path, monkeypatch, caplog):
    review = tmp_path / "_ai_review.json"
    review.write_text("not JSON")
    reports = tmp_path / "reports"

    _run(
        monkeypatch,
        "--run-id",
        "run-1",
        "--submission-name",
        "demo",
        "--report-dir",
        str(reports),
        "--review-file",
        str(review),
    )

    assert "Could not read review tokens" in caplog.text
    assert not reports.exists()
