"""Exercise the M365 check with structured events and collector artifacts."""

import json
from pathlib import Path

import yaml


EVAL = Path(__file__).resolve().parents[1] / "submissions/openclaw-forge/eval.yaml"


def _check(outputs):
    judge = next(
        j for j in yaml.safe_load(EVAL.read_text())["judges"]
        if j["name"] == "used_m365_tools"
    )
    namespace = {}
    source = "def check(outputs, arguments):\n" + "".join(
        "    " + line + "\n" for line in judge["check"].splitlines()
    )
    exec(compile(source, "<used_m365_tools>", "exec"), namespace)
    return namespace["check"](outputs, judge["arguments"])


def _record(message_count=2):
    coverage = [
        {"id": "email", "value": str(message_count)},
        {"id": "meetings", "value": "0"},
    ]
    evidence = {
        "sealed": True,
        "requestedSources": ["microsoft365"],
        "unavailable": [],
        "evidenceId": "evidence-1",
        "coverage": coverage,
        "microsoft365": {
            "account": {"mail": "tbx-demo2@dev.mscloud.ibm.com"},
            "messages": [{"id": f"message-{i}"} for i in range(message_count)],
            "events": [],
        },
    }
    index = {"evidenceId": "evidence-1", "coverage": coverage}
    return {
        "output_content": "Outlook M365 Graph was used",
        "events": [
            {"type": "assistant", "tools": [{
                "id": "call-1", "name": "exec", "input": {
                    "command": "node tools/collect-brief-evidence.mjs"
                },
            }]},
            {"type": "tool_result", "tool_use_id": "call-1",
             "tool_name": "exec", "is_error": False},
        ],
        "files": {
            ".openclaw/tmp/brief.evidence.json/brief.evidence.json": json.dumps(evidence),
            ".openclaw/tmp/brief.index.json/brief.index.json": json.dumps(index),
        },
    }


def test_m365_scorer_uses_observed_count_below_collection_limit():
    assert _check(_record(message_count=2)) is True


def test_m365_scorer_account_matches_scene():
    judge = next(
        j for j in yaml.safe_load(EVAL.read_text())["judges"]
        if j["name"] == "used_m365_tools"
    )
    scene = yaml.safe_load((EVAL.parent / "scenes/monday-acquisition.yaml").read_text())
    assert judge["arguments"]["expected_account"] == scene["m365"]["user"]


def test_m365_scorer_rejects_keyword_only_or_failed_collection():
    record = _record()
    record["events"] = []
    assert _check(record) is False

    record = _record()
    record["events"][1]["is_error"] = True
    assert _check(record) is False

    record = _record()
    record["events"][1]["tool_use_id"] = "different-call"
    assert _check(record) is False


def test_m365_scorer_requires_consistent_retrieved_records():
    record = _record()
    record["files"].pop(".openclaw/tmp/brief.evidence.json/brief.evidence.json")
    assert _check(record) is False

    record = _record()
    evidence_path = ".openclaw/tmp/brief.evidence.json/brief.evidence.json"
    evidence = json.loads(record["files"][evidence_path])
    evidence["microsoft365"]["account"]["mail"] = "other@example.com"
    record["files"][evidence_path] = json.dumps(evidence)
    assert _check(record) is False

    record = _record()
    index_path = ".openclaw/tmp/brief.index.json/brief.index.json"
    index = json.loads(record["files"][index_path])
    index["coverage"][0]["value"] = "400"
    record["files"][index_path] = json.dumps(index)
    assert _check(record) is False

    assert _check(_record(message_count=0)) is False


def test_m365_scorer_accepts_flat_archived_artifact_paths():
    record = _record()
    record["files"] = {
        path.rsplit("/", 1)[0]: content
        for path, content in record["files"].items()
    }
    assert _check(record) is True
