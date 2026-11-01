"""A full-run seal needs a paired call and a verified sealed evidence ID."""

import copy
import json
from pathlib import Path

import yaml


EVAL = Path(__file__).resolve().parents[1] / "submissions/openclaw-forge/eval.yaml"
FULL = "0f2cf381-092f-4a10-89f5-86461fc51892"
ATTENTION = "00bc3dc2-7ad1-4880-9ab2-e5a0a768b2fb"


def _judge():
    return next(x for x in yaml.safe_load(EVAL.read_text())["judges"]
                if x["name"] == "full_evidence_sealed")


def _check(events, files=None):
    ns = {}
    source = "def check(outputs, arguments):\n" + "".join(
        "    " + line + "\n" for line in _judge()["check"].splitlines()
    )
    exec(compile(source, "<full_evidence_sealed>", "exec"), ns)
    return ns["check"]({"events": events, "files": files or {}}, {})


def _call(call_id, command):
    return {"type": "assistant", "tools": [{"id": call_id, "name": "exec",
            "input": {"command": command}}]}


def _result(call_id, content, is_error=False):
    return {"type": "tool_result", "tool_use_id": call_id,
            "tool_name": "exec", "is_error": is_error, "result": content}


def _events(seal_result="[Malformed diagnostic JSON redacted]"):
    return [
        _call("s1", "node tools/collect-brief-evidence.mjs"),
        _result("s1", "[Oversized diagnostic JSON redacted]"),
        _call("z1", "node tools/collect-brief-evidence.mjs seal"),
        _result("z1", seal_result),
    ]


def _files(sealed=True, id=FULL, previous_scope="attention"):
    common = {"schemaVersion": 1, "evidenceId": id,
              "collectedAt": "2026-10-08T10:59:12Z",
              "sealedAt": "2026-10-08T11:00:00Z", "sealed": sealed}
    evidence = {**common, "reconciliation": {"previous": {
        "scope": previous_scope, "evidenceId": ATTENTION}}}
    return {
        ".openclaw/tmp/brief.evidence.json/brief.evidence.json": json.dumps(evidence),
        ".openclaw/tmp/brief.index.json/brief.index.json": json.dumps(common),
    }


def test_direct_full_seal_result_passes():
    result = (f"sealed governed briefing evidence {FULL} "
              "(Email 400; full run, 40m 0s left to publish)")
    assert _judge()["if"] == "annotations.get('requires_published_brief')"
    assert _check(_events(result)) is True
    assert _check(_events(result)[:2]) is False


def test_redacted_seal_uses_matching_manifest_and_index():
    assert _check(_events(), _files()) is True
    assert _check(_events(), _files(sealed=False)) is False
    assert _check(_events(), _files(previous_scope=None)) is False
    bad = _files()
    bad[".openclaw/tmp/brief.index.json/brief.index.json"] = json.dumps(
        {"sealed": True, "evidenceId": ATTENTION})
    assert _check(_events(), bad) is False


def test_call_or_nonerror_failure_does_not_pass():
    assert _check(_events()[:3], _files()) is False
    failed = _events("brief evidence seal failed: expired window")
    assert _check(failed, _files()) is False
    error = _events()
    error[-1]["is_error"] = True
    assert _check(error, _files()) is False
    unpaired = _events()
    unpaired[-1]["tool_use_id"] = "other"
    assert _check(unpaired, _files()) is False


def test_retry_needs_own_seal_evidence():
    failed = _events("brief evidence seal failed: expired window")
    retry = [_call("z2", "node tools/collect-brief-evidence.mjs seal"),
             _result("z2", "[Malformed diagnostic JSON redacted]")]
    assert _check(failed + retry, _files()) is True
    old_archive = _events() + [
        _call("c1", "node tools/compose-brief.mjs .openclaw/tmp/items-full.json"),
        _result("c1", f"composed full brief candidate for evidence {FULL} (topOfMind 3)"),
    ]
    assert _check(old_archive) is False
    wrong = copy.deepcopy(old_archive)
    wrong[-1]["tool_use_id"] = "other"
    assert _check(wrong) is False
