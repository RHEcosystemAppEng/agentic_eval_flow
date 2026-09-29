#!/usr/bin/env python3
"""Build sealed cases with a pinned image checkout's own collector.

No provider calls: only `seal` is invoked. Commit generated fixtures with the
submission; regeneration is explicit and must use the recorded source revision.
"""

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

CLOCK = "2026-09-24T12:00:00Z"
CLOCK_MODULE = """// Freeze managed briefing tools only; never gateway timers.
const RealDate = Date;
const fixed = RealDate.parse('2026-09-24T12:00:00Z');
globalThis.Date = class extends RealDate {
  constructor(...args) { super(...(args.length ? args : [fixed])); }
  static now() { return fixed; }
};
"""
PROMPT = """Evaluate the daily-briefing skill using the supplied sealed evidence.
The collector sweep and context stages have already completed. Resume the full
workflow at fan-out using the batch plan and context in eval/sealed.json.
Do not sweep, drill, read providers, or change the supplied evidence/prior brief.
Drafting is unavailable in this briefing-only case: publish grounded items without
proposal links. Run composer and publisher with `node --import ./eval/clock.mjs`
so their clock matches the sealed fixture. Use the image-owned managed tools and
the normal brief-reader profile. Publish brief.json; report briefly when complete.
"""


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def validate_evidence(workspace, evidence):
    """Use the image-owned publication schema before committing a fixture."""
    code = (
        "import {validateBriefEvidence} from "
        + json.dumps((workspace / "tools/publish-brief.mjs").as_uri())
        + "; import fs from 'node:fs';"
        + "console.log(JSON.stringify(validateBriefEvidence(JSON.parse(fs.readFileSync("
        + json.dumps(str(evidence))
        + ", 'utf8')))));"
    )
    issues = json.loads(subprocess.check_output(["node", "--input-type=module", "-e", code], text=True))
    if not isinstance(issues, list) or issues:
        raise ValueError("invalid evidence fixture: " + json.dumps(issues))


def mail(identity, subject, body, conversation=None):
    return dict(
        id=identity,
        subject=subject,
        span=subject + " " + body,
        conversationId=conversation or identity,
        receivedDateTime="2026-09-24T11:00:00Z",
        from_="reviewer@example.test",
        toRecipients=[{"emailAddress": {"address": "executive@example.test"}}],
        **{"from": {"emailAddress": {"address": "reviewer@example.test", "name": "Morgan"}}},
    )


def build(image_repo, target):
    revision = subprocess.check_output(["git", "-C", str(image_repo), "rev-parse", "HEAD"], text=True).strip()
    memo = mail(
        "platform-review",
        "Platform constraints memo",
        "Please review API rate limits, scaling thresholds, and orchestration dependencies by September 28. "
        "Feedback is required before leadership finalizes phased infrastructure upgrades.",
    )
    noise = mail(
        "automated-noise", "Daily build digest", "Automated successful build. No action required. Do not reply."
    )
    other = mail(
        "independent-review",
        "Platform hiring review",
        "Please approve the independent platform hiring budget by September 25. "
        "This is a separate decision from the infrastructure memo.",
    )
    definitions = [
        ("decision-deadline", [memo, noise], [], {"topOfMind": ["m365:platform-review"]}, ["m365:automated-noise"], {}),
        (
            "distinct-actions",
            [memo, other],
            [],
            {"topOfMind": ["m365:platform-review", "m365:independent-review"]},
            [],
            {},
        ),
        (
            "cross-source-action",
            [memo],
            [
                {
                    "channel": "CPLATFORM",
                    "ts": "1790249400.000001",
                    "thread_ts": "1790249400.000001",
                    "user": "UMORGAN",
                    "text": "Reminder: the platform constraints memo review is the same September 28 "
                    "action Morgan emailed.",
                    "span": "Reminder: the platform constraints memo review is the same September 28 "
                    "action Morgan emailed.",
                }
            ],
            {"topOfMind": ["m365:platform-review"]},
            [],
            {
                "equivalent_actions": [
                    ["m365:platform-review", "slack:CPLATFORM|1790249400.000001", "m365:platform-calendar"]
                ],
                "events": [
                    {
                        "id": "platform-calendar",
                        "subject": "Platform memo feedback deadline",
                        "span": "Reminder only for the same platform constraints memo feedback due September 28. "
                        "No separate meeting or action.",
                        "start": {"dateTime": "2026-09-28T12:00:00Z", "timeZone": "UTC"},
                        "end": {"dateTime": "2026-09-28T12:15:00Z", "timeZone": "UTC"},
                    }
                ],
            },
        ),
        ("empty", [], [], {}, [], {"max_items": 0}),
        (
            "unavailable-truncated",
            [memo],
            [],
            {"topOfMind": ["m365:platform-review"]},
            [],
            {"unavailable": ["slack"], "truncated": True},
        ),
    ]
    for name, messages, slack, expected, excluded, extra in definitions:
        case = target / "cases" / name
        if case.exists():
            raise ValueError("refusing to replace existing fixture: " + str(case))
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            shutil.copytree(image_repo / "agents/chief-of-staff/workspace/tools", workspace / "tools")
            (workspace / "eval").mkdir()
            (workspace / "eval/clock.mjs").write_text(CLOCK_MODULE)
            evidence = {
                "schemaVersion": 1,
                "evidenceId": "forge-eval-v1-" + name,
                "collectedAt": CLOCK,
                "sealed": False,
                "window": {"sinceIso": "2026-09-21T12:00:00Z"},
                "requestedSources": ["microsoft365", "slack"],
                "unavailable": extra.get("unavailable", []),
                "unavailableReasons": [{"source": "slack", "code": "not-configured"}]
                if extra.get("unavailable")
                else [],
                "coverage": [],
                "context": {
                    "user": {
                        "displayName": "Alex Rivera",
                        "role": "Engineering executive",
                        "initials": "AR",
                        "primaryEmail": "executive@example.test",
                        "timeZone": "UTC",
                    },
                    "preferences": None,
                    "issues": [],
                },
                "reconciliation": {"previous": {"scope": "full"}, "items": []},
                "microsoft365": {
                    "account": {"id": "eval-executive", "displayName": "Alex Rivera", "mail": "executive@example.test"},
                    "messages": messages,
                    "events": extra.get("events", []),
                    "truncated": {"messages": bool(extra.get("truncated"))},
                },
                "slack": {
                    "account": {"teamId": "TEVAL", "userId": "UEXEC"},
                    "conversations": [{"id": "CPLATFORM", "name": "platform"}] if slack else [],
                    "users": [],
                    "messages": slack,
                },
            }
            dump(workspace / ".openclaw/tmp/brief.evidence.json", evidence)
            # A structurally valid empty full brief fixes the refresh path.
            prior = {
                "schemaVersion": 1,
                "evidenceId": "prior-fixture",
                "generatedAt": "2026-09-23T12:00:00Z",
                "generatedFor": "Alex Rivera",
                "scope": "full",
                "greeting": {
                    "name": "Alex",
                    "role": "Engineering executive",
                    "initials": "AR",
                    "date": "Wednesday, September 23",
                    "summary": {"lead": "No pending items.", "tail": ""},
                },
                "notifications": [],
                "coverage": [
                    {"id": i, "label": label, "value": "0"}
                    for i, label in [
                        ("email", "Email"),
                        ("slack", "Slack Messages"),
                        ("meetings", "Meetings"),
                        ("library", "Library Artifacts"),
                    ]
                ],
                "topOfMind": [],
                "fyi": [],
                "lookingAhead": [],
            }
            dump(workspace / "brief.json", prior)
            result = subprocess.check_output(
                [
                    "node",
                    "--import",
                    str(workspace / "eval/clock.mjs"),
                    str(workspace / "tools/collect-brief-evidence.mjs"),
                    "seal",
                ],
                text=True,
            )
            sealed = json.loads(result.split("--- brief.sealed.json ---\n")[1].strip())
            validate_evidence(workspace, workspace / ".openclaw/tmp/brief.evidence.json")
            dump(workspace / "eval/sealed.json", sealed)
            shutil.copytree(workspace / ".openclaw", case / ".openclaw")
            shutil.copytree(workspace / "eval", case / "eval")
            shutil.copy2(workspace / "brief.json", case / "brief.json")
            dump(case / "input.yaml", {"prompt": PROMPT})  # JSON is valid YAML.
            data = (case / ".openclaw/tmp/brief.evidence.json").read_bytes()
            manifest = {
                "version": 1,
                "as_of": CLOCK,
                "evidence": ".openclaw/tmp/brief.evidence.json",
                "sha256": hashlib.sha256(data).hexdigest(),
                "generator_image_source": revision,
                "generator_tool_hashes": {
                    p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (workspace / "tools").glob("*.mjs")
                },
                "expected": expected,
                "excluded": excluded,
                **{k: v for k, v in extra.items() if k != "events"},
            }
            dump(case / "eval-fixture.json", manifest)
    return len(definitions)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-repo", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print("Generated", build(args.image_repo.resolve(), args.output.resolve()), "sealed fixtures")
