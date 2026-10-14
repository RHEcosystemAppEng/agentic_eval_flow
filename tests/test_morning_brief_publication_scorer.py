"""The publication gate checks Forge briefing v1 shape and evidence binding."""

import copy
import json
from pathlib import Path

import yaml


EVAL = Path(__file__).resolve().parents[1] / "submissions/openclaw-forge/eval.yaml"


def _judge():
    return next(
        judge for judge in yaml.safe_load(EVAL.read_text())["judges"]
        if judge["name"] == "published_brief"
    )


def _check(outputs):
    judge = _judge()
    namespace = {}
    source = "def check(outputs, arguments):\n" + "".join(
        "    " + line + "\n" for line in judge["check"].splitlines()
    )
    exec(compile(source, "<published_brief>", "exec"), namespace)
    return namespace["check"](outputs, {})


def _record():
    coverage = [
        {"id": "email", "label": "Email", "value": "2"},
        {"id": "slack", "label": "Slack Messages", "value": "0"},
        {"id": "meetings", "label": "Meetings", "value": "0"},
        {"id": "library", "label": "Library Artifacts", "value": "0"},
    ]
    brief = {
        "schemaVersion": 1,
        "evidenceId": "evidence-1",
        "generatedAt": "2026-10-08T10:00:00Z",
        "generatedFor": "Test Executive",
        "scope": "full",
        "greeting": {
            "name": "Test", "role": "Executive", "initials": "TE", "date": "Thursday, October 8",
            "summary": {"lead": "Two items need review.", "tail": ""},
        },
        "notifications": [],
        "coverage": coverage,
        "topOfMind": [{
            "id": "m365:message-1", "source": "email", "provider": "microsoft365",
            "meta": "Example sender", "title": "Decision needed",
            "description": "Review the request today.",
        }],
        "fyi": [{
            "id": "m365:message-2", "source": "email", "provider": "microsoft365",
            "meta": "Example sender", "body": "Status update.",
        }],
        "lookingAhead": [],
    }
    return {
        "files": {
            "brief.json/brief.json": json.dumps(brief),
            ".openclaw/tmp/brief.evidence.json/brief.evidence.json": json.dumps({
                "sealed": True, "evidenceId": "evidence-1", "coverage": coverage,
            }),
            ".openclaw/tmp/brief.index.json/brief.index.json": json.dumps({
                "evidenceId": "evidence-1", "coverage": coverage,
            }),
        }
    }


def _change_brief(record, mutate):
    result = copy.deepcopy(record)
    key = "brief.json/brief.json"
    brief = json.loads(result["files"][key])
    mutate(brief)
    result["files"][key] = json.dumps(brief)
    return result


def test_full_forge_brief_with_empty_optional_section_passes():
    assert _check(_record()) is True
    assert _judge()["if"] == "annotations.get('requires_published_brief')"
    annotations = yaml.safe_load((EVAL.parent / "cases/morning-briefing/annotations.yaml").read_text())
    assert annotations["requires_published_brief"] is True


def test_stub_or_missing_forge_sections_fail():
    record = _change_brief(_record(), lambda brief: brief.pop("topOfMind"))
    assert _check(record) is False
    record = _change_brief(_record(), lambda brief: brief.update({"fyi": "not an array"}))
    assert _check(record) is False
    record = _change_brief(_record(), lambda brief: brief.update({"schemaVersion": 2}))
    assert _check(record) is False


def test_invalid_card_and_coverage_fail():
    record = _change_brief(_record(), lambda brief: brief["topOfMind"][0].pop("description"))
    assert _check(record) is False
    record = _change_brief(_record(), lambda brief: brief["fyi"][0].update({"source": "draft"}))
    assert _check(record) is False
    record = _change_brief(_record(), lambda brief: brief["coverage"][0].update({"value": "400"}))
    assert _check(record) is False


def test_scope_and_evidence_binding_fail_closed():
    record = _change_brief(_record(), lambda brief: brief.update({"scope": "attention"}))
    assert _check(record) is False
    record = _change_brief(_record(), lambda brief: brief.update({"evidenceId": "other"}))
    assert _check(record) is False
    record = _record()
    record["files"].pop(".openclaw/tmp/brief.evidence.json/brief.evidence.json")
    assert _check(record) is False


def test_flat_archived_paths_are_accepted():
    record = _record()
    record["files"] = {
        path.rsplit("/", 1)[0]: text for path, text in record["files"].items()
    }
    assert _check(record) is True
