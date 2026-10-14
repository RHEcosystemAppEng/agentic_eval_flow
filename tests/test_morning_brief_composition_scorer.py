"""A full candidate needs the composer's matched success result."""

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


def _judge():
    return next(x for x in yaml.safe_load(EVAL.read_text())["judges"]
                if x["name"] == "full_candidate_composed")


def _archive():
    if ARCHIVE is None or not ARCHIVE.is_dir():
        raise SkipTest("Set MORNING_BRIEF_ARCHIVE to run archived-case checks")
    return ARCHIVE


def _check(events, files):
    ns = {}
    source = "def check(outputs, arguments):\n" + "".join(
        "    " + line + "\n" for line in _judge()["check"].splitlines()
    )
    exec(compile(source, "<full_candidate_composed>", "exec"), ns)
    return ns["check"]({"events": events, "files": files}, {})


def _files():
    common = {"evidenceId": FULL, "sealed": True,
              "sealedAt": "2026-10-08T11:00:00Z"}
    evidence = {**common, "reconciliation": {"previous": {
        "scope": "attention", "evidenceId": ATTENTION}}}
    return {
        ".openclaw/tmp/brief.evidence.json/brief.evidence.json": json.dumps(evidence),
        ".openclaw/tmp/brief.index.json/brief.index.json": json.dumps(common),
    }


def _archive_files():
    archive = _archive()
    return {
        f".openclaw/tmp/{name}/{name}":
            (archive / ".openclaw/tmp" / name).read_text()
        for name in ("brief.evidence.json", "brief.index.json")
    }


def _call(call_id="c1", command="node tools/compose-brief.mjs .openclaw/tmp/items-full.json"):
    return {"type": "assistant", "tools": [{"id": call_id, "name": "exec",
            "input": {"command": command}}]}


def _result(call_id="c1", text=None, is_error=False):
    if text is None:
        text = (f"composed full brief candidate for evidence {FULL} "
                "(topOfMind 3, fyi 5, lookingAhead 0; no drafts notification)\n"
                "publish it with: node tools/publish-brief.mjs\n")
    return {"type": "tool_result", "tool_use_id": call_id,
            "tool_name": "exec", "is_error": is_error, "result": text}


def test_archived_failed_attempt_then_successful_retry():
    events = json.loads((_archive() / "events.json").read_text())
    files = _archive_files()
    assert _judge()["if"] == "annotations.get('requires_published_brief')"
    assert _check(events, files) is True
    # The first full compose failed with eight quote problems and shell exit 1.
    first_success_call = next(i for i, ev in enumerate(events)
                              if any(t.get("id") == "chatcmpl-tool-ac1c7ddbff40ba69"
                                     for t in ev.get("tools", [])))
    assert _check(events[:first_success_call], files) is False
    without_success = [ev for ev in events if ev.get("tool_use_id") !=
                       "chatcmpl-tool-ac1c7ddbff40ba69"]
    assert _check(without_success, files) is False


def test_call_failure_or_unmatched_result_cannot_pass():
    assert _check([_call()], _files()) is False
    assert _check([_call(), _result(text="the brief was not composed, 8 problems:\n"
                         "(Command exited with code 1)")], _files()) is False
    assert _check([_call(), _result(is_error=True)], _files()) is False
    assert _check([_call(), _result(call_id="other")], _files()) is False
    assert _check([_call(command="node tools/publish-brief.mjs"), _result()],
                  _files()) is False


def test_full_current_sealed_evidence_id_required():
    events = [_call(), _result()]
    assert _check(events, _files()) is True
    assert _check([_call(), _result(text=_result()["result"].replace(
        FULL, ATTENTION))], _files()) is False
    files = _files()
    index = json.loads(files[".openclaw/tmp/brief.index.json/brief.index.json"])
    index["evidenceId"] = ATTENTION
    files[".openclaw/tmp/brief.index.json/brief.index.json"] = json.dumps(index)
    assert _check(events, files) is False
    files = _files()
    manifest = json.loads(files[".openclaw/tmp/brief.evidence.json/brief.evidence.json"])
    manifest["sealed"] = False
    files[".openclaw/tmp/brief.evidence.json/brief.evidence.json"] = json.dumps(manifest)
    assert _check(events, files) is False


def test_retry_and_exit_marker():
    failed = [_call(), _result(text="compose failed: invalid input\n"
              "(Command exited with code 1)")]
    retry = [_call("c2"), _result("c2")]
    assert _check(failed + retry, _files()) is True
    bad = copy.deepcopy(retry)
    bad[-1]["result"] += "(Command exited with code 1)\n"
    assert _check(bad, _files()) is False
