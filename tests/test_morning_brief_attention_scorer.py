"""Attention publication requires a verified, paired publisher read-back."""

import copy
from pathlib import Path

import yaml


EVAL = Path(__file__).resolve().parents[1] / "submissions/openclaw-forge/eval.yaml"
EVIDENCE_ID = "00bc3dc2-7ad1-4880-9ab2-e5a0a768b2fb"


def _judge():
    return next(
        judge for judge in yaml.safe_load(EVAL.read_text())["judges"]
        if judge["name"] == "attention_brief_published"
    )


def _check(events):
    namespace = {}
    source = "def check(outputs, arguments):\n" + "".join(
        "    " + line + "\n" for line in _judge()["check"].splitlines()
    )
    exec(compile(source, "<attention_brief_published>", "exec"), namespace)
    return namespace["check"]({"events": events}, {})


def _events():
    return [
        {"type": "assistant", "tools": [{"id": "compose-1", "name": "exec", "input": {
            "command": "node tools/compose-brief.mjs .openclaw/tmp/items-attention.json"}}]},
        {"type": "tool_result", "tool_use_id": "compose-1", "tool_name": "exec",
         "is_error": False, "result": f"composed attention brief candidate for evidence {EVIDENCE_ID} (topOfMind 2)"},
        {"type": "assistant", "tools": [{"id": "publish-1", "name": "exec", "input": {
            "command": "node tools/publish-brief.mjs"}}]},
        {"type": "tool_result", "tool_use_id": "publish-1", "tool_name": "exec",
         "is_error": False, "result": (
             "published governed brief.json, verified after write\n"
             "--- brief.json (summary) ---\n"
             f'{{"evidenceId":"{EVIDENCE_ID}","scope":"attention","generatedAt":"..."}}'
         )},
    ]


def test_verified_attention_publication_passes():
    assert _judge()["if"] == "annotations.get('requires_published_brief')"
    assert _check(_events()) is True


def test_call_or_composition_alone_does_not_prove_publication():
    assert _check(_events()[:3]) is False
    events = _events()
    events[3]["result"] = "Command failed with exit code 1"
    assert _check(events) is False
    events = _events()
    events[3]["is_error"] = True
    assert _check(events) is False


def test_mismatched_result_id_or_wrong_scope_fails():
    events = _events()
    events[3]["tool_use_id"] = "other"
    assert _check(events) is False
    events = _events()
    events[3]["result"] = events[3]["result"].replace('"attention"', '"full"')
    assert _check(events) is False


def test_failed_attempt_then_verified_retry_passes():
    events = _events()
    events[3]["result"] = "Command failed with exit code 1"
    retry = copy.deepcopy(_events()[2:])
    retry[0]["tools"][0]["id"] = "publish-2"
    retry[1]["tool_use_id"] = "publish-2"
    assert _check(events + retry) is True


def test_publisher_readback_does_not_require_compose_event():
    events = _events()
    assert _check(events[2:]) is True
