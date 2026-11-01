"""Full-sweep continuation scores independently of attention publication."""

import copy
import json
from pathlib import Path

import yaml


EVAL = Path(__file__).resolve().parents[1] / "submissions/openclaw-forge/eval.yaml"
FULL = "0f2cf381-092f-4a10-89f5-86461fc51892"


def _judge():
    return next(x for x in yaml.safe_load(EVAL.read_text())["judges"]
                if x["name"] == "continued_to_full_run")


def _check(events, files=None):
    ns = {}
    source = "def check(outputs, arguments):\n" + "".join(
        "    " + line + "\n" for line in _judge()["check"].splitlines()
    )
    exec(compile(source, "<continued_to_full_run>", "exec"), ns)
    return ns["check"]({"events": events, "files": files or {}}, {})


def _call(call_id, command):
    return {"type": "assistant", "tools": [{"id": call_id, "name": "exec",
            "input": {"command": command}}]}


def _result(call_id, content, is_error=False):
    return {"type": "tool_result", "tool_use_id": call_id,
            "tool_name": "exec", "is_error": is_error, "result": content}


def _sweep(call_id, content):
    return [_call(call_id, "node tools/collect-brief-evidence.mjs"),
            _result(call_id, content)]


def _files(full_id=FULL, previous_scope="attention"):
    common = {"schemaVersion": 1, "evidenceId": full_id,
              "collectedAt": "2026-10-08T10:59:12Z"}
    evidence = {**common, "reconciliation": {"previous": {
        "scope": previous_scope,
        "evidenceId": "00bc3dc2-7ad1-4880-9ab2-e5a0a768b2fb"}}}
    return {
        ".openclaw/tmp/brief.evidence.json/brief.evidence.json": json.dumps(evidence),
        ".openclaw/tmp/brief.index.json/brief.index.json": json.dumps(common),
    }


def test_successful_later_full_sweep_passes_without_attention_publication():
    events = _sweep("s1", "[Oversized diagnostic JSON redacted]") + _sweep(
        "s2", f"this is a full run\ncollected governed briefing evidence {FULL} since 2026-10-08"
    )
    assert _judge()["if"] == "annotations.get('requires_published_brief')"
    assert _check(events) is True
    assert _check(events[2:]) is False  # A single refresh sweep is not a continuation.
    assert _check(events[:2]) is False


def test_redacted_later_sweep_needs_collector_artifacts():
    events = _sweep("s1", "[Oversized diagnostic JSON redacted]") + _sweep(
        "s2", "[Oversized diagnostic JSON redacted]"
    )
    assert _check(events) is False
    assert _check(events, _files()) is True
    assert _check(events, _files(previous_scope=None)) is False
    assert _check(events, _files(full_id="11111111-1111-1111-1111-111111111111")) is True


def test_failed_later_sweep_requires_successful_retry():
    events = _sweep("s1", "[Oversized diagnostic JSON redacted]") + _sweep(
        "s2", "brief evidence collection failed: no requested governed briefing source was available"
    )
    assert _check(events) is False
    events += _sweep("s3", "[Oversized diagnostic JSON redacted]") + [
        _call("c3", "node tools/compose-brief.mjs .openclaw/tmp/items-full.json"),
        _result("c3", f"composed full brief candidate for evidence {FULL} (topOfMind 3)"),
    ]
    assert _check(events, _files()) is True


def test_unpaired_sweep_result_does_not_pass():
    events = _sweep("s1", "[Oversized diagnostic JSON redacted]") + _sweep(
        "s2", f"this is a full run\ncollected governed briefing evidence {FULL} since 2026-10-08"
    )
    broken = copy.deepcopy(events)
    broken[-1]["tool_use_id"] = "other"
    assert _check(broken) is False
