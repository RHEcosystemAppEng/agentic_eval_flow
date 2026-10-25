# AEH runtime for Konflux skill evaluation

This repository supplies the images and Python code used by
`konflux-shared-tasks/task/aeh-skill-eval`. That shared Tekton Task owns the
workflow and fans out once per catalog skill. AEH runs through Harbor on
Kubernetes/OpenShift, in single or control/treatment pairwise mode.

The supported flow is:

1. Materialize the caller's trusted source artifact into a private workspace per skill.
2. Always validate skill structure and scan the skill with Cisco Skill Scanner and harness-eval.
3. Validate and review an opted-in, changed skill with an AEH submission.
4. Run AEH in separate trial pods and aggregate its judge results.
5. Optionally produce scorecards, push Compass Facts, attach OCI reports, and store results in Postgres/MinIO.
6. Emit independent Tier A and Tier B Konflux results. Tier B is `SKIPPED` when evaluation does not apply.

Persistence is independently gated by `enable-persistence`. It can store
scan-only scorecards for skills without AEH submissions or without an opted-in
evaluation. Scanner findings remain available even when a scan exits nonzero.

## Images

All five images share `quay.io/ecosystem-appeng/agent-eval-harness`; each
purpose has its own tag. Build them from this repository's root:

```sh
make images IMAGE_REPOSITORY=quay.io/ecosystem-appeng/agent-eval-harness VERSION=v3.0
```

| Purpose tag suffix | Containerfile | Used by |
|---|---|---|
| `-task` | [task utilities](containers/aeh-task/Containerfile) | Source preparation, validation, MCP configuration rewriting, advisory review, reporting, scorecards, storage, result serialization |
| `-security-scan` | [Cisco scanner](containers/security-scan/Containerfile) | `security-scan` step |
| `-skillmd-scan` | [SKILL.md scanner](containers/skillmd-scan/Containerfile) | `skillmd-scan` step |
| `-runner` | [orchestrator](containers/agent-eval-harness/Containerfile) | `run-eval` step; creates trial pods and performs suite-level scoring |
| `-trial` | [trial runtime](containers/aeh-trial/Containerfile) | Trial pods; Claude Code, stdio mock MCP server, AEH reward verifier |

The shared task selects these independently through `task-image`,
`security-scan-image`, `skillmd-scan-image`, `runner-image`, and `aeh-image`.
`aeh-image` means the **trial pod image**. External trusted-artifact and ORAS
steps retain their Konflux images.

See [the published test release](Docs/test-release-20261019.md),
[image builds and release wiring](Docs/images.md),
[the shared task contract](Docs/konflux-integration-guide.md),
[submission files](Docs/submission-formats.md), and
[persistence](Docs/persistence.md).

## Development

```sh
uv sync --extra dev --extra observability --locked
make test
uv run --locked --extra dev ruff check abevalflow scripts tests containers
uv run --locked --extra dev ruff format --check abevalflow scripts tests containers
```

Validate a local submission:

```sh
uv run --locked python scripts/validate.py submissions/aeh-pairwise-example \
  --eval-engine aeh --aeh-mode pairwise
```

`abevalflow/` holds the AEH score adapter, OpenShift environment and task
enrichment, metadata/report contracts, gates, certification, telemetry, and DB
models. `scripts/` contains the entry points called by the shared task.
`containers/` defines the five runtime purposes. `config/` contains the skill
certification profile and optional persistence secret templates. Examples are
AEH submissions; the shared task and its bundle release live in
`konflux-shared-tasks`.
