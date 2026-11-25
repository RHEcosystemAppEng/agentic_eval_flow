"""The full workflow must complete in sequence for one evidence set."""

import copy
import json
import os
from pathlib import Path
from unittest import SkipTest

import yaml


ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "submissions/openclaw-forge/eval.yaml"
ARCHIVE = Path(os.environ["MORNING_BRIEF_ARCHIVE"]) if os.environ.get("MORNING_BRIEF_ARCHIVE") else None
FULL_SWEEP = "chatcmpl-tool-99f240595042b870"
FULL_SEAL = "chatcmpl-tool-a219d5bc24996740"
GOOD_COMPOSE = "chatcmpl-tool-ac1c7ddbff40ba69"
FULL_PUBLISH = "chatcmpl-tool-9deaf494cbbb238d"
FULL = "0f2cf381-092f-4a10-89f5-86461fc51892"
ATTENTION = "00bc3dc2-7ad1-4880-9ab2-e5a0a768b2fb"


def _judge():
    return next(x for x in yaml.safe_load(EVAL.read_text())["judges"]
                if x["name"] == "full_workflow_order")


def _check(events, files):
    ns = {}
    source = "def check(outputs, arguments):\n" + "".join(
        "    " + line + "\n" for line in _judge()["check"].splitlines()
    )
    exec(compile(source, "<full_workflow_order>", "exec"), ns)
    result = ns["check"]({"events": events, "files": files}, {})
    assert isinstance(result, tuple) and isinstance(result[0], bool)
    assert isinstance(result[1], str) and result[1]
    return result


def _archive():
    if ARCHIVE is None or not ARCHIVE.is_dir():
        raise SkipTest("Set MORNING_BRIEF_ARCHIVE to run archived-case checks")
    events = json.loads((ARCHIVE / "events.json").read_text())
    files = {"brief.json/brief.json": (ARCHIVE / "brief.json").read_text()}
    for name in ("brief.evidence.json", "brief.index.json"):
        files[f".openclaw/tmp/{name}/{name}"] = (
            ARCHIVE / ".openclaw/tmp" / name).read_text()
    return events, files


def _fixture():
    """Small source-free sequence that CI can score without the private archive."""
    common = {"schemaVersion": 1, "evidenceId": FULL, "sealed": True,
              "collectedAt": "2026-10-08T10:00:02Z",
              "sealedAt": "2026-10-08T10:00:04Z"}
    evidence = {**common, "reconciliation": {"previous": {
        "scope": "attention", "evidenceId": ATTENTION}},
        "batches": [{"id": "batch-1", "path": ".openclaw/tmp/batches/batch-1.json"}]}
    files = {
        "brief.json/brief.json": json.dumps({"scope": "full", "evidenceId": FULL}),
        ".openclaw/tmp/brief.evidence.json/brief.evidence.json": json.dumps(evidence),
        ".openclaw/tmp/brief.index.json/brief.index.json": json.dumps(common),
    }
    def call(call_id, name, input_, second):
        return {"type": "assistant", "timestamp": f"2026-10-08T10:00:{second:02d}Z",
                "tools": [{"id": call_id, "name": name, "input": input_}]}
    def result(call_id, name, text, second):
        return {"type": "tool_result", "timestamp": f"2026-10-08T10:00:{second:02d}Z",
                "tool_use_id": call_id, "tool_name": name, "is_error": False,
                "result": text}
    events = [
        call("sweep", "exec", {"command": "node tools/collect-brief-evidence.mjs"}, 1),
        result("sweep", "exec", "[Oversized diagnostic JSON redacted]", 3),
        call("seal", "exec", {"command": "node tools/collect-brief-evidence.mjs seal"}, 3),
        result("seal", "exec", "[Malformed diagnostic JSON redacted]", 5),
        call("spawn", "sessions_spawn", {"agentId": "brief-reader",
             "task": "Read `.openclaw/tmp/batches/batch-1.json`"}, 6),
        result("spawn", "sessions_spawn", json.dumps({"status": "accepted"}), 7),
        call("read", "read", {"path": "$WORKSPACE_DIR/.openclaw/tmp/batches/batch-1.result.json"}, 8),
        result("read", "read", json.dumps({"schemaVersion": 1, "evidenceId": FULL,
               "batchId": "batch-1", "items": []}), 9),
        call("compose-bad", "exec", {"command": "node tools/compose-brief.mjs items.json"}, 10),
        result("compose-bad", "exec", "the brief was not composed\n(Command exited with code 1)", 11),
        call("compose", "exec", {"command": "node tools/compose-brief.mjs items.json"}, 12),
        result("compose", "exec", f"composed full brief candidate for evidence {FULL} "
               "(topOfMind 1, fyi 0, lookingAhead 0; no drafts notification)\n"
               "publish it with: node tools/publish-brief.mjs\n", 13),
        call("publish", "exec", {"command": "node tools/publish-brief.mjs"}, 14),
        result("publish", "exec", "[Malformed diagnostic JSON redacted]", 15),
    ]
    return events, files


def test_portable_workflow_sequence_and_failures():
    events, files = _fixture()
    assert _check(events, files)[0] is True
    for call_id, stage in (("sweep", "full sweep"), ("seal", "full seal"),
                           ("read", "batch review"), ("compose", "successful full compose"),
                           ("publish", "full publication")):
        passed, reason = _check(_without_result(events, call_id), files)
        assert passed is False and stage in reason
    moved = _move_call_and_result(events, "compose", 6)
    assert "successful full compose" in _check(moved, files)[1]
    moved = _move_call_and_result(events, "publish", 10)
    assert "full publication" in _check(moved, files)[1]


def test_portable_zero_batch_plan():
    events, files = _fixture()
    key = ".openclaw/tmp/brief.evidence.json/brief.evidence.json"
    evidence = json.loads(files[key])
    evidence["batches"] = []
    files[key] = json.dumps(evidence)
    events = [ev for ev in events if ev.get("tool_use_id") not in ("spawn", "read")
              and not any(t.get("id") in ("spawn", "read") for t in ev.get("tools", []))]
    assert _check(events, files)[0] is True


def _without_result(events, call_id):
    return [ev for ev in events if ev.get("tool_use_id") != call_id]


def _move_call_and_result(events, call_id, before):
    events = copy.deepcopy(events)
    moved = []
    kept = []
    for ev in events:
        if ev.get("tool_use_id") == call_id:
            moved.append(ev)
        elif ev.get("type") == "assistant" and any(
                t.get("id") == call_id for t in ev.get("tools", [])):
            moved.append(ev)
        else:
            kept.append(ev)
    assert len(moved) == 2
    return kept[:before] + moved + kept[before:]


def test_diagnostic_archive_has_complete_order_with_retry():
    events, files = _archive()
    assert _judge()["if"] == "annotations.get('requires_published_brief')"
    passed, reason = _check(events, files)
    assert passed is True and "in order" in reason


def test_missing_stage_reports_first_unverified_step():
    events, files = _archive()
    for call_id, expected in ((FULL_SWEEP, "full sweep"),
                              (FULL_SEAL, "full seal"),
                              (GOOD_COMPOSE, "successful full compose"),
                              (FULL_PUBLISH, "full publication")):
        passed, reason = _check(_without_result(events, call_id), files)
        assert passed is False and expected in reason
    batch_14 = next(t["id"] for ev in events for t in ev.get("tools", [])
                    if t.get("name") == "read" and
                    str(t.get("input", {}).get("path", "")).endswith(
                        "/batch-14.result.json"))
    passed, reason = _check(_without_result(events, batch_14), files)
    assert passed is False and "batch review" in reason


def test_reordered_compose_or_publish_fails():
    events, files = _archive()
    # Move the successful retry before all batch results. The first compose
    # remains after the reads, but it failed and cannot satisfy this stage.
    early_compose = _move_call_and_result(events, GOOD_COMPOSE, 140)
    passed, reason = _check(early_compose, files)
    assert passed is False and "successful full compose" in reason
    early_publish = _move_call_and_result(events, FULL_PUBLISH, 165)
    passed, reason = _check(early_publish, files)
    assert passed is False and "full publication" in reason


def test_redacted_stage_needs_matching_artifact_time():
    events, files = _archive()
    manifest_key = ".openclaw/tmp/brief.evidence.json/brief.evidence.json"
    index_key = ".openclaw/tmp/brief.index.json/brief.index.json"
    manifest = json.loads(files[manifest_key])
    index = json.loads(files[index_key])
    manifest["collectedAt"] = index["collectedAt"] = "2026-10-08T12:00:00Z"
    altered = {**files, manifest_key: json.dumps(manifest), index_key: json.dumps(index)}
    passed, reason = _check(events, altered)
    assert passed is False and "full sweep" in reason
    manifest = json.loads(files[manifest_key])
    index = json.loads(files[index_key])
    manifest["sealedAt"] = index["sealedAt"] = "2026-10-08T12:00:00Z"
    files[manifest_key] = json.dumps(manifest)
    files[index_key] = json.dumps(index)
    passed, reason = _check(events, files)
    assert passed is False and "full seal" in reason


def test_zero_batch_plan_skips_reader_stage():
    events, files = _archive()
    key = ".openclaw/tmp/brief.evidence.json/brief.evidence.json"
    manifest = json.loads(files[key])
    manifest["batches"] = []
    files[key] = json.dumps(manifest)
    # Existing reader events are irrelevant to a declared zero-batch plan.
    assert _check(events, files)[0] is True
