"""End-to-end checks for the supported AEH report and CLI contracts."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from abevalflow.engines import get_all_engines, get_engine
from abevalflow.report import AnalysisResult
from abevalflow.schemas import SubmissionMetadata
from scripts.aggregate_aeh import aggregate_single_run
from scripts.analyze import main as render_report

ROOT = Path(__file__).resolve().parents[1]


def test_aggregate_report_renders_without_changing_verdict(tmp_path):
    run = tmp_path / "my-skill" / "run-1"
    run.mkdir(parents=True)
    (run / "run_result.json").write_text(
        json.dumps(
            {
                "run_id": "run-1",
                "per_case": {"case-1": {"judges": {"quality": {"value": 0.9}}}},
            }
        )
    )
    (run / "summary.yaml").write_text(
        "run_id: run-1\nmean_reward: 0.9\n"
        "per_case:\n  case-1:\n    quality:\n      value: 0.9\n"
        "judges:\n  quality:\n    mean: 0.9\n    pass_rate: 1.0\n"
    )
    report = tmp_path / "report.json"
    report.write_text(json.dumps(aggregate_single_run(run)))
    original = report.read_bytes()
    result = AnalysisResult.model_validate_json(original)

    assert render_report(["--report-json", str(report)]) == 0
    rendered = report.with_suffix(".md").read_text()
    assert result.submission_name in rendered
    assert result.summary.recommendation.value.upper() in rendered
    assert report.read_bytes() == original


@pytest.mark.parametrize("engine", ["harbor", "ase", "both", "a2a", "mcpchecker", "aeh_openshell_openclaw"])
def test_removed_engines_are_rejected(engine):
    assert get_all_engines() == ["aeh"]
    with pytest.raises(KeyError):
        get_engine(engine)
    with pytest.raises(ValidationError):
        SubmissionMetadata(name="my-skill", eval_engine=engine)


def test_publisher_accepts_shared_task_arguments(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "scripts/publish.py",
            "--report-dir",
            str(tmp_path),
            "--submission-name",
            "my-skill",
            "--pipeline-run-id",
            "run-1",
            "--eval-engine",
            "aeh",
            "--workspace-root",
            str(tmp_path),
            "--report-prefix",
            "my-prefix",
        ],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_repository_submissions_validate():
    """Validate every checked-in submission, without keeping a stale path list."""
    from scripts.validate import validate_submission

    submissions = sorted(path for path in (ROOT / "submissions").iterdir() if path.is_dir())
    assert submissions, "expected at least one checked-in submission"

    for submission in submissions:
        mode = "pairwise" if (submission / "eval-control.yaml").exists() else "single"
        errors = validate_submission(submission, aeh_mode=mode)
        assert errors == [], f"{submission.relative_to(ROOT)} failed validation: {errors}"


@pytest.mark.parametrize("field", ["generation_mode", "red_team", "mcp", "security_scan_use_llm"])
def test_removed_metadata_fields_are_rejected(field):
    with pytest.raises(ValidationError):
        SubmissionMetadata(name="my-skill", **{field: None})
