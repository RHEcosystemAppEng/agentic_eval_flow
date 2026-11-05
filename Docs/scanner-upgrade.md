# harness-eval 8.0.1 compatibility

CLI 0.7.1 pins `harness-eval==8.0.1`. The upstream package is installed
unchanged; no scanner fork, monkeypatch, suppression or threshold change is
required. Cisco remains pinned to 2.3.1, and AEH/Harbor are unchanged.

## Existing workflow contract

Upstream 8 removed `skill-submission-scan` and the `SKILL_SUBMISSION` preset.
Its new `harness-lint` command defaults to advice rules, which would omit the
security rules previously run by this workflow. The CLI therefore invokes
`python3 -m abevalflow.validation.quality`, a small adapter over the upstream Python rule engine.
It explicitly selects all 21 retained submission-profile rules: nine security
checks at error severity and twelve content/quality checks at warning severity.
Every required rule must be present and execute; a missing rule or scanner
exception fails the operation rather than producing a clean score.

The adapter keeps the existing `skillmd-quality-scan.json` and
`skillmd-security-scan.json` files, finding fields, severity mapping and local
or container execution. High/critical findings still fail; completed warning
findings remain warnings. Scanner crashes, incomplete outputs and timeouts
remain errors. Existing scoring, Compass/PostgreSQL facts, signed score
projection, report generation and tier selection are unchanged.

The raw reports additionally record scanner version, the versioned adapter
profile, executed rule IDs, suppression count and content-check coverage.
Security checks continue to inspect supporting Markdown as well as SKILL.md.

The default `quality-command=harness-eval` setting selects this adapter.
Explicit custom executable overrides keep the legacy invocation contract:
`COMMAND skill-submission-scan PATH --output-quality FILE --output-security FILE`.
Custom overrides own their scanner implementation and version. Container quality
images must include CLI 0.7.1's adapter module and its pinned
dependencies; the standard runner image includes both.

## Root and source handling

For `PLUGIN/skills/SKILL`, the adapter receives the staged plugin directory as
`project_root`. Standalone skills use their staged source root. It scans the
selected skill only, without attributing other skills' findings to it.
The existing source stager resolves internal links before scanning. Scanning
the raw authoring tree can produce misleading missing-file findings when a
skill's links resolve into another skill; comparisons must use staged content.

## Upstream rule migration

| Previous check | Treatment |
|---|---|
| Nine `security/*` rules | All retained, with the previous error/critical mapping. |
| Description, format, token budget, references, unfinished content and other retained quality checks | Same selected rule IDs and configured severities; upstream fixes apply. |
| `quality/scope-overreach`, `quality/trigger-manipulation` | Consolidated upstream into the already selected `quality/scope-grab-description`. |
| `quality/negative-only`, `quality/example-gap` | Retired upstream as subjective style heuristics. Not recreated locally. |
| `submission/file-completeness` | Its instruction-body length and test-assertion checks are preserved in the adapter, with their original finding ID and severities. |

These retirements are explicit behavior changes from the upstream major
release, rather than accidental loss from switching command defaults. Genuine
broken dependencies, empty sections and active security findings remain tested.
The released [8.0.1 package](https://pypi.org/project/harness-eval/8.0.1/)
contains the authoritative upstream implementation.

## rh-sre local comparison

A local rescan of all 13 staged skills, compared with the downloaded fe2528a
build report, gives:

| Skill | Previous quality | Upgraded quality |
|---|---:|---:|
| cve-impact | 86 | 90 |
| cve-validation | 100 | 100 |
| execution-summary | 0 | 60 |
| fleet-inventory | 86 | 90 |
| job-template-creator | 96 | 100 |
| job-template-remediation-validator | 92 | 98 |
| mcp-aap-validator | 98 | 100 |
| mcp-lightspeed-validator | 98 | 100 |
| playbook-executor | 90 | 100 |
| playbook-generator | 78 | 86 |
| remediation | 84 | 90 |
| remediation-verifier | 90 | 100 |
| system-context | 72 | 78 |

Execution-summary still has four medium broken-reference findings for generic
path templates, a comma-separated output example and `scripts/tools` prose.
Its score of 60 meets the unchanged internal threshold exactly. This upgrade
does not claim to fix every scanner false positive. No skill text or acceptance
policy was changed to obtain these results.

This is a local compatibility check, not fresh signed evidence. Activation
still requires a new runner image, an approved digest in the scoped EC policy,
updated build pins, and a fresh Konflux build/readiness test. Do not reuse the
old Snapshot's score evidence as if it came from the upgraded scanner.

Validation: the complete regression suite passed 624 tests with 4 skips; the
additional staged cross-skill-link regression also passed. Ruff and whitespace
checks passed. A local-only image layering the verified upgrade wheels onto
the existing runner passed a full network-isolated Tier 1 run under Python
3.12.15: structure/security passed, quality completed with warnings and score
60, report generation passed, and the unchanged AEH runtime validated. This
smoke image was not pushed and live runtime pins remain unchanged.
