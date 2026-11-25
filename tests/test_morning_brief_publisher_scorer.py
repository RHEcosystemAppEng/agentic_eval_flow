"""The publisher must be called and leave the current full brief on disk."""

import copy
import json
import os
from pathlib import Path
from unittest import SkipTest

import yaml


ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "submissions/openclaw-forge/eval.yaml"
ARCHIVE = Path(os.environ["MORNING_BRIEF_ARCHIVE"]) if os.environ.get("MORNING_BRIEF_ARCHIVE") else None
FULL = "0f2cf381-092f-4a10-89f5-86461fc51892"
ATTENTION = "00bc3dc2-7ad1-4880-9ab2-e5a0a768b2fb"
PUBLISH_ID = "chatcmpl-tool-9deaf494cbbb238d"


def _judge():
    return next(x for x in yaml.safe_load(EVAL.read_text())["judges"]
                if x["name"] == "full_publisher_succeeded")


def _check(events, files):
    ns = {}
    source = "def check(outputs, arguments):\n" + "".join(
        "    " + line + "\n" for line in _judge()["check"].splitlines()
    )
    exec(compile(source, "<full_publisher_succeeded>", "exec"), ns)
    result = ns["check"]({"events": events, "files": files}, {})
    assert isinstance(result, tuple) and isinstance(result[0], bool)
    assert isinstance(result[1], str) and result[1]
    return result


def _archive_files():
    if ARCHIVE is None or not ARCHIVE.is_dir():
        raise SkipTest("Set MORNING_BRIEF_ARCHIVE to run archived-case checks")
    files = {"brief.json/brief.json": (ARCHIVE / "brief.json").read_text()}
    for name in ("brief.evidence.json", "brief.index.json"):
        files[f".openclaw/tmp/{name}/{name}"] = (
            ARCHIVE / ".openclaw/tmp" / name).read_text()
    return files


def _files():
    common = {"schemaVersion": 1, "evidenceId": FULL, "sealed": True,
              "sealedAt": "2026-10-08T11:00:00Z"}
    return {
        "brief.json/brief.json": json.dumps({"scope": "full", "evidenceId": FULL}),
        ".openclaw/tmp/brief.evidence.json/brief.evidence.json": json.dumps(common),
        ".openclaw/tmp/brief.index.json/brief.index.json": json.dumps(common),
    }


def _call(call_id="p1"):
    return {"type": "assistant", "timestamp": "2026-10-08T11:06:15.454Z",
            "tools": [{"id": call_id, "name": "exec",
            "input": {"command": "node tools/publish-brief.mjs"}}]}


def _result(call_id="p1", text="[Malformed diagnostic JSON redacted]",
            is_error=False):
    return {"type": "tool_result", "tool_use_id": call_id,
            "tool_name": "exec", "is_error": is_error, "result": text}


def _direct_success():
    return ("published governed brief.json, verified after write\n"
            "--- brief.json (summary) ---\n"
            + json.dumps({"evidenceId": FULL, "scope": "full"}) + "\n")


def test_archived_full_publish_uses_saved_file_for_redacted_result():
    files = _archive_files()
    events = json.loads((ARCHIVE / "events.json").read_text())
    assert _judge()["if"] == "annotations.get('requires_published_brief')"
    passed, reason = _check(events, files)
    assert passed is True and "redacted" in reason
    # Preserve the real archive's attention publication; remove only its full call.
    without_call = copy.deepcopy(events)
    for ev in without_call:
        if ev.get("type") == "assistant":
            ev["tools"] = [t for t in ev.get("tools", []) if t.get("id") != PUBLISH_ID]
    passed, reason = _check(without_call, files)
    assert passed is False and "did not call" in reason
    without_result = [ev for ev in events if ev.get("tool_use_id") != PUBLISH_ID]
    passed, reason = _check(without_result, files)
    assert passed is False and "matching tool result" in reason


def test_read_back_and_saved_file_must_agree():
    events = [_call(), _result(text=_direct_success())]
    assert _check(events, _files())[0] is True
    files = _files()
    del files["brief.json/brief.json"]
    passed, reason = _check(events, files)
    assert passed is False and "no readable brief.json" in reason
    files = _files()
    brief = json.loads(files["brief.json/brief.json"])
    brief["evidenceId"] = ATTENTION
    files["brief.json/brief.json"] = json.dumps(brief)
    passed, reason = _check(events, files)
    assert passed is False and "not the current full brief" in reason
    bad_summary = _direct_success().replace(FULL, ATTENTION)
    assert _check([_call(), _result(text=bad_summary)], _files())[0] is False


def test_failure_reason_and_retry():
    failed = [_call(), _result(text="brief candidate refused\n(Command exited with code 1)")]
    passed, reason = _check(failed, _files())
    assert passed is False and "reported failure" in reason
    assert _check(failed + [_call("p2"), _result("p2", _direct_success())],
                  _files())[0] is True
    assert _check([_call(), _result(is_error=True)], _files())[0] is False
    assert _check([_call(), _result(call_id="other")], _files())[0] is False


def test_current_sealed_ledger_is_required():
    files = _files()
    index = json.loads(files[".openclaw/tmp/brief.index.json/brief.index.json"])
    index["evidenceId"] = ATTENTION
    files[".openclaw/tmp/brief.index.json/brief.index.json"] = json.dumps(index)
    passed, reason = _check([_call(), _result()], files)
    assert passed is False and "sealed full-run evidence" in reason
