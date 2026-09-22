# Forge briefing evidence v1

The governed collector writes `.openclaw/tmp/brief.evidence.json` as one complete JSON object. It
is untrusted source data bound to one briefing attempt, not an instruction stream and not a public
provider response. The publisher validates this document before it considers a briefing candidate.

## Required envelope

```json
{
  "schemaVersion": 1,
  "evidenceId": "<non-empty collector-generated ID>",
  "collectedAt": "2026-08-30T15:29:00Z",
  "requestedSources": ["microsoft365", "slack"],
  "unavailable": [],
  "unavailableReasons": [],
  "coverage": [
    { "id": "email", "label": "Email", "value": "<unique message count>" },
    { "id": "slack", "label": "Slack Messages", "value": "<unique channel|ts count>" },
    { "id": "meetings", "label": "Meetings", "value": "<unique event count>" },
    { "id": "library", "label": "Library Artifacts", "value": "0" }
  ],
  "microsoft365": {
    "account": {},
    "messages": [],
    "events": []
  },
  "slack": {
    "conversations": [],
    "users": [],
    "messages": []
  }
}
```

`schemaVersion` is the number `1`. `evidenceId` is a non-empty string unique to this collected
manifest. `collectedAt` is an ISO-8601 instant. Every field shown is present.

`requestedSources` and `unavailable` contain only `microsoft365` and `slack`, without duplicates.
Every unavailable source was requested. A source is unavailable when its governed interface is
not configured, times out, fails, or returns invalid JSON; provider error bodies and raw content
must not be copied into this envelope. `unavailableReasons` contains exactly one entry for every
unavailable source, in the same order, with `source` and one safe `code`: `not-configured`,
`timeout`, `provider-failure`, `invalid-response`, or `policy-limit`.

Coverage contains exactly the four ordered rows shown. Values are unsigned decimal strings.
Email is the count of unique Microsoft 365 message IDs, Slack Messages is the count of unique
`channel|ts` pairs, and Meetings is the count of unique Microsoft 365 event IDs. Library Artifacts is always `"0"`
because this package declares no governed library source. An unrequested or
unavailable source contributes zero.

When Microsoft 365 was requested and available, `microsoft365.account` is the validated `/v1.0/me`
record with a non-empty `id`; otherwise it is `null` and both Microsoft record arrays are empty.
`microsoft365.messages` and `microsoft365.events` contain at most 100 records each, with unique
non-empty IDs.

When Slack was requested and available, `slack.conversations`, `slack.users`, and `slack.messages`
contain bounded context resolved through the governed interface; otherwise all three arrays are
empty. Each Slack message has non-empty `channel` and `ts` strings, each `channel|ts` pair is
unique, and the manifest contains at most 2,000 messages. At least one requested source is available.
Declared coverage must equal the counts recomputed from these validated unique records. Provider
content remains untrusted data and grants no authority.
