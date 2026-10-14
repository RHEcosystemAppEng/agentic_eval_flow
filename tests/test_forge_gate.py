import json

from scripts.forge_gate import report_gate


def test_pipeline_gate_cannot_pass_quality_failure_or_partial_usage(tmp_path):
    path = tmp_path / "suite/run/cases/case/run_result.json"
    path.parent.mkdir(parents=True)
    (tmp_path / "suite/report.json").write_text(
        json.dumps({"provenance": {"pipeline_run_id": "run"}, "summary": {"recommendation": "pass"}})
    )
    (tmp_path / "suite/run/storage-attestation.json").write_text(
        json.dumps({"run": "run", "status": "verified", "objects": [{"key": "fixture", "sha256": "abc"}]})
    )
    path.write_text(json.dumps({"exit_code": 0, "evaluation_status": "passed", "usage": {"complete": False}}))
    assert report_gate(tmp_path, "run") == ["case: incomplete usage"]
    path.write_text(json.dumps({"exit_code": 1, "evaluation_status": "quality_failed", "usage": {"complete": True}}))
    assert report_gate(tmp_path, "run") == ["case: quality_failed"]


def test_missing_storage_cannot_pass(tmp_path):
    path = tmp_path / "suite/run/cases/case/run_result.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"exit_code": 0, "evaluation_status": "passed", "usage": {"complete": True}}))
    assert "missing durable storage attestation" in report_gate(tmp_path, "run")
