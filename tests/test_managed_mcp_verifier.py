"""Exercise the smoke verdict against actual normalization and SQLite persistence."""

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from abevalflow import mcp_ci
from scripts import verify_managed_mcp_smoke as smoke
from scripts.aggregate_mcpchecker import aggregate_mcpchecker_results
from scripts.store_results import store


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'smoke.db'}")
    for key, value in {
        "MINIO_ENDPOINT": "http://storage",
        "MINIO_ACCESS_KEY": "test",
        "MINIO_SECRET_KEY": "test",
    }.items():
        monkeypatch.setenv(key, value)
    objects = {}
    for uid, passed in [("positive-uid", True), ("negative-uid", False)]:
        raw = json.loads((Path(__file__).parent / "fixtures/mcpchecker/assertion-failure.json").read_text())
        raw["results"][0]["allAssertionsPassed"] = passed
        raw["results"][0]["assertionResults"]["minToolCalls"]["passed"] = passed
        file = tmp_path / (uid + ".json")
        file.write_text(json.dumps(raw))
        report = aggregate_mcpchecker_results(file, uid, pipeline_run_id=uid)
        folder = tmp_path / uid
        folder.mkdir()
        (folder / "mcpchecker-report.json").write_text(report.model_dump_json())
        assert store(folder, run_id=uid)
        assert store(folder, run_id=uid)  # retry is not a second row
        prefix = f"mcp-runs/{uid}/"
        payload = {
            "workspace/eval-results/probe/mcpchecker-out.json": raw,
            "workspace/reports/probe/mcpchecker-report.json": report.model_dump(mode="json"),
            "workspace/eval-results/probe/process-status.json": {
                "uid": uid,
                "exit_code": 0,
                "timed_out": False,
                "agent_model": "openai:claude-sonnet",
                "judge_model": "openai:claude-sonnet",
            },
            "workspace/_mcp_runs/probe/readiness.json": {"ready": True, "tools": ["get_cve_by_id"]},
            "workspace/_mcp_runs/probe/target.json": {
                "uid": uid,
                "name": "mcp-fixture",
                "descriptor": {"image": "example@sha256:" + "a" * 64},
            },
            "workspace/_mcp_runs/probe/pod-status/mcp-fixture.json": {
                "status": {"container_statuses": [{"image_id": "container@sha256:" + "a" * 64}]}
            },
            "workspace/_mcp_runs/probe/context.json": {"uid": uid},
        }
        for name, value in payload.items():
            objects[prefix + name] = json.dumps(value).encode()
        objects[prefix + "pod-logs/mcp-fixture.log"] = b'{"event": "tool_call", "success": true}'
        objects[prefix + "workspace/eval-results/probe/mcpchecker-process.log"] = b"native process output"
        entries = [
            {"key": key, "bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}
            for key, value in objects.items()
            if key.startswith(prefix)
        ]
        objects[prefix + "archive-complete.json"] = json.dumps(
            {"uid": uid, "complete": True, "objects": entries}
        ).encode()
        objects[prefix + "cleanup.json"] = b'{"deleted":true}'

    class Response(io.BytesIO):
        def release_conn(self):
            pass

    monkeypatch.setattr(smoke, "Minio", lambda *a, **k: NS(get_object=lambda bucket, key: Response(objects[key])))
    claims = []
    monkeypatch.setattr(
        mcp_ci,
        "Cluster",
        lambda *a: NS(
            namespace="test",
            list=lambda *a: [],
            core=NS(list_namespaced_persistent_volume_claim=lambda *a, **k: NS(items=claims)),
        ),
    )
    return objects, claims


def test_smoke_only_passes_with_both_controls_and_unique_persisted_rows(evidence):
    result = smoke.verify("positive-uid", "negative-uid")
    assert result["overall_score"] == 1.0 and result["recommendation"] == "pass"
    assert [control["score"] for control in result["controls"]] == [1.0, 0.0]


def test_omitted_required_evidence_is_not_a_smoke_pass(evidence):
    objects, _ = evidence
    key = "mcp-runs/positive-uid/archive-complete.json"
    manifest = json.loads(objects[key])
    manifest["objects"] = [entry for entry in manifest["objects"] if not entry["key"].endswith("/process-status.json")]
    objects[key] = json.dumps(manifest).encode()
    with pytest.raises(AssertionError, match="Required evidence"):
        smoke.verify("positive-uid", "negative-uid")


def test_changed_download_and_retained_pvc_fail_smoke(evidence):
    objects, claims = evidence
    key = "mcp-runs/positive-uid/workspace/eval-results/probe/mcpchecker-process.log"
    original = objects[key]
    objects[key] = b"corrupted"
    with pytest.raises(AssertionError):
        smoke.verify("positive-uid", "negative-uid")
    objects[key] = original
    claims.append(NS())
    with pytest.raises(AssertionError, match="PVC not deleted"):
        smoke.verify("positive-uid", "negative-uid")
