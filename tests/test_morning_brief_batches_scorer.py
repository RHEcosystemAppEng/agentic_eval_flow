"""The parent must review every declared full-run reader result."""

import copy
import json
import os
from pathlib import Path
from unittest import SkipTest

import yaml


ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "submissions/openclaw-forge/eval.yaml"
ARCHIVE = Path(os.environ["MORNING_BRIEF_ARCHIVE"]) if os.environ.get("MORNING_BRIEF_ARCHIVE") else None
EVIDENCE_ID = "0f2cf381-092f-4a10-89f5-86461fc51892"


def _judge():
    return next(x for x in yaml.safe_load(EVAL.read_text())["judges"]
                if x["name"] == "full_batches_reviewed")


def _archive():
    if ARCHIVE is None or not ARCHIVE.is_dir():
        raise SkipTest("Set MORNING_BRIEF_ARCHIVE to run archived-case checks")
    return ARCHIVE


def _check(events, files):
    ns = {}
    source = "def check(outputs, arguments):\n" + "".join(
        "    " + line + "\n" for line in _judge()["check"].splitlines()
    )
    exec(compile(source, "<full_batches_reviewed>", "exec"), ns)
    return ns["check"]({"events": events, "files": files}, {})


def _files(batch_ids=("batch-1", "batch-2")):
    common = {"evidenceId": EVIDENCE_ID, "sealed": True,
              "sealedAt": "2026-10-08T11:00:00Z"}
    evidence = {**common, "batches": [{"id": bid,
        "path": f".openclaw/tmp/batches/{bid}.json"} for bid in batch_ids]}
    return {
        ".openclaw/tmp/brief.evidence.json/brief.evidence.json": json.dumps(evidence),
        ".openclaw/tmp/brief.index.json/brief.index.json": json.dumps(common),
    }


def _events(batch_ids=("batch-1", "batch-2")):
    events = []
    for bid in batch_ids:
        events += [
            {"type": "assistant", "tools": [{"id": "spawn-" + bid,
                "name": "sessions_spawn", "input": {"agentId": "brief-reader",
                "task": f"Read `.openclaw/tmp/batches/{bid}.json`"}}]},
            {"type": "tool_result", "tool_use_id": "spawn-" + bid,
                "tool_name": "sessions_spawn", "is_error": False,
                "result": json.dumps({"status": "accepted"})},
            {"type": "assistant", "tools": [{"id": "read-" + bid,
                "name": "read", "input": {"path":
                f"$WORKSPACE_DIR/.openclaw/tmp/batches/{bid}.result.json"}}]},
            {"type": "tool_result", "tool_use_id": "read-" + bid,
                "tool_name": "read", "is_error": False,
                "result": json.dumps({"schemaVersion": 1, "evidenceId": EVIDENCE_ID,
                "batchId": bid, "items": []})},
        ]
    return events


def test_archived_diagnostic_run_passes():
    archive = _archive()
    manifest = (archive / ".openclaw/tmp/brief.evidence.json").read_text()
    index = (archive / ".openclaw/tmp/brief.index.json").read_text()
    files = {
        ".openclaw/tmp/brief.evidence.json/brief.evidence.json": manifest,
        ".openclaw/tmp/brief.index.json/brief.index.json": index,
    }
    assert _judge()["if"] == "annotations.get('requires_published_brief')"
    assert _check(json.loads((archive / "events.json").read_text()), files) is True


def test_archived_run_missing_or_wrong_result_fails():
    archive = _archive()
    files = {
        ".openclaw/tmp/brief.evidence.json/brief.evidence.json":
            (archive / ".openclaw/tmp/brief.evidence.json").read_text(),
        ".openclaw/tmp/brief.index.json/brief.index.json":
            (archive / ".openclaw/tmp/brief.index.json").read_text(),
    }
    original = json.loads((archive / "events.json").read_text())
    events = copy.deepcopy(original)
    read_id = next(t["id"] for ev in events for t in ev.get("tools", [])
                   if t.get("name") == "read" and
                   str(t.get("input", {}).get("path", "")).endswith(
                       "/batch-14.result.json"))
    events = [ev for ev in events if ev.get("tool_use_id") != read_id]
    assert _check(events, files) is False
    events = copy.deepcopy(original)
    result = next(ev for ev in events if ev.get("tool_use_id") == read_id)
    body = json.loads(result["result"])
    body["evidenceId"] = "wrong"
    result["result"] = json.dumps(body)
    assert _check(events, files) is False


def test_all_batches_and_zero_batch_plan():
    assert _check(_events(), _files()) is True
    assert _check([], _files(())) is True
    assert _check(_events(("batch-1",)), _files()) is False


def test_failed_or_unmatched_spawn_does_not_pass():
    events = _events()
    events[1]["result"] = json.dumps({"status": "forbidden"})
    assert _check(events, _files()) is False
    events = _events()
    events[1]["tool_use_id"] = "other"
    assert _check(events, _files()) is False
    events = _events()
    events[0]["tools"][0]["input"]["agentId"] = "other-reader"
    assert _check(events, _files()) is False


def test_retry_and_matching_parent_read():
    events = _events()
    retry = copy.deepcopy(events[:2])
    retry[0]["tools"][0]["id"] = "retry"
    retry[1]["tool_use_id"] = "retry"
    events[1]["result"] = json.dumps({"status": "forbidden"})
    assert _check(events[:2] + retry + events[2:], _files()) is True
    for field, value in (("evidenceId", "wrong"), ("batchId", "batch-2"),
                         ("items", "invalid")):
        bad = _events()
        result = json.loads(bad[3]["result"])
        result[field] = value
        bad[3]["result"] = json.dumps(result)
        assert _check(bad, _files()) is False
    bad = _events()
    bad[3]["tool_use_id"] = "other"
    assert _check(bad, _files()) is False


def test_sealed_plan_and_order_required():
    files = _files()
    index = json.loads(files[".openclaw/tmp/brief.index.json/brief.index.json"])
    index["sealed"] = False
    files[".openclaw/tmp/brief.index.json/brief.index.json"] = json.dumps(index)
    assert _check(_events(), files) is False
    events = _events()
    assert _check(events[2:4] + events[:2] + events[4:], _files()) is False
