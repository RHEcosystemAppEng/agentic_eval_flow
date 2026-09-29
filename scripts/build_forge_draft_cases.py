#!/usr/bin/env python3
"""Generate proposal-only lifecycle cases with fresh conversations and stored state."""

import json
from pathlib import Path

import yaml


def build(output):
    payload = {
        "channel": "mail",
        "provider": "microsoft365",
        "context_ref": "m365:platform-review",
        "to": ["morgan@example.test"],
        "subject": "RE: Platform constraints memo",
        "body": "I will review the constraints before September 28.",
    }
    first = "1" * 32
    second = "2" * 32

    def seed(state="proposed", identity=first, payload=payload):
        return dict(draft_id=identity, state=state, payload=payload)

    request = (
        "Prepare a reply to Morgan at morgan@example.test for email m365:platform-review "
        "(subject Platform constraints memo). Say I will send specific feedback on API rate limits "
        "and scaling thresholds before September 28, in a warm professional tone."
    )
    cases = [
        (
            "new",
            [],
            request,
            {"count": 1, "created_actions": [{"context_ref": "m365:platform-review", "to": ["morgan@example.test"]}]},
        ),
        ("tone-revision", [seed()], request, {"count": 1, "revised": [first]}),
        (
            "brief-refresh",
            [seed()],
            "The briefing refreshed m365:platform-review. Keep the existing reply if it already says "
            "I will review the constraints before September 28. The destination is morgan@example.test.",
            {"count": 1, "unchanged": [first]},
        ),
        ("accepted-fresh-chat", [seed("accepted")], request, {"count": 1, "unchanged": [first]}),
        ("discarded-fresh-chat", [seed("discarded")], request, {"count": 1, "unchanged": [first]}),
        (
            "distinct-destination",
            [seed()],
            request + " Also prepare a separate reply for the same source addressed to taylor@example.test "
            "requesting scaling measurements.",
            {"count": 2, "created_actions": [{"context_ref": "m365:platform-review", "to": ["taylor@example.test"]}]},
        ),
        (
            "similar-title-distinct-source",
            [seed()],
            request.replace("m365:platform-review", "m365:different-platform-review"),
            {
                "count": 2,
                "unchanged": [first],
                "created_actions": [{"context_ref": "m365:different-platform-review", "to": ["morgan@example.test"]}],
            },
        ),
        (
            "publication-failure",
            [seed()],
            request + " The previous briefing failed to publish, and this is a fresh conversation.",
            {"count": 1, "revised": [first]},
        ),
        (
            "ambiguous",
            [seed(), seed(identity=second, payload={**payload, "body": "I can review shortly."})],
            request,
            {"count": 2, "unchanged": [first, second]},
        ),
        (
            "uncertain-create",
            [],
            request,
            {"count": 1, "created_actions": [{"context_ref": "m365:platform-review", "to": ["morgan@example.test"]}]},
        ),
        (
            "greenfield-batch",
            [],
            "Prepare three separate email reply proposals: sources m365:review-a, m365:review-b, m365:review-c, "
            "each addressed to morgan@example.test with subject RE: Review and body I will review it by September 28.",
            {
                "count": 3,
                "max_all_state_lists": 1,
                "created_actions": [{"context_ref": "m365:review-" + x, "to": ["morgan@example.test"]} for x in "abc"],
            },
        ),
    ]
    for name, drafts, prompt, expected in cases:
        if name in (
            "new",
            "tone-revision",
            "publication-failure",
            "similar-title-distinct-source",
            "uncertain-create",
            "distinct-destination",
        ):
            source = (
                "m365:different-platform-review" if name == "similar-title-distinct-source" else "m365:platform-review"
            )
            expected["content"] = [
                {
                    "match": {"context_ref": source, "to": ["morgan@example.test"]},
                    "body_patterns": [
                        "API rate limits",
                        "scaling thresholds",
                        r"(?:September|Sept?\.?) 28|28 (?:September|Sept?\.?)",
                    ],
                }
            ]
        if name == "distinct-destination":
            expected["content"].append(
                {"match": {"to": ["taylor@example.test"]}, "body_patterns": ["scaling", "measurements"]}
            )
        if name == "greenfield-batch":
            expected["content"] = [
                {"match": action, "body_patterns": ["review", r"(?:September|Sept?\.?) 28|28 (?:September|Sept?\.?)"]}
                for action in expected["created_actions"]
            ]
        root = output / "cases" / name
        if root.exists():
            raise ValueError("refusing to overwrite fixture " + name)
        (root / "eval").mkdir(parents=True)
        (root / "input.yaml").write_text(
            yaml.safe_dump(
                {
                    "prompt": prompt + " These are explicit user-verified destinations and source references. "
                    "Follow the forge-drafts skill. Only prepare proposals; never send. Do not modify evaluation files."
                }
            )
        )
        (root / "eval/drafts-seed.json").write_text(
            json.dumps({"drafts": drafts, "lose_create_response": name == "uncertain-create"}, indent=2)
        )
        (root / "draft-expectations.json").write_text(json.dumps(expected, indent=2))
    return len(cases)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    print("Generated", build(p.parse_args().output), "draft cases")
