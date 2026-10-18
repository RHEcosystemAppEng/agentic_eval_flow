# AEH submissions

Catalog skills live under `<pack>/skills/<skill>/SKILL.md`. An optional AEH
submission lives at `submissions/<pack>/<skill>/` and contains:

```text
metadata.yaml
eval.yaml                         # single mode
eval-control.yaml                 # pairwise mode
eval-treatment.yaml               # pairwise mode
cases/<case-id>/input.yaml
cases/<case-id>/annotations.yaml   # optional ground truth
tool_handlers.yaml                # required when inputs.tools is configured
```

Minimal metadata:

```yaml
name: my-skill
eval_engine: aeh
aeh_mode: single
```

`aeh_mode: pairwise` selects separate control and treatment configs. Override
their filenames with `aeh_control_config` and `aeh_treatment_config`.
`abevalflow/schemas.py` defines supported metadata, including gate policy,
Compass Facts, certification policy, resource limits, and advisory-review
settings. Unknown fields are rejected. `security_scan` remains accepted for
existing catalog metadata; the shared task always runs deterministic Tier A
scans, and `gate_policy` controls scorecard treatment. The unused
`security_scan_use_llm` input has been removed. `experiment.n_trials` remains accepted
for existing catalog metadata; AEH's own configuration controls execution.

Each eval config declares `models.skill` (or a model string), judges, and the
case dataset. `runner.plugin_dirs` entries resolve relative to the submission
directory and contain the relevant `SKILL.md` files. If `skill` is declared,
it must match metadata's `name` so report paths agree across steps.

For pairwise evaluation, the treatment config must declare output paths and a
pairwise LLM judge. When case annotations exist, its prompt must reference
`{{ annotations }}` so comparisons use the documented ground truth. Declare
numeric judge `score_range` explicitly; aggregation uses each judge's range
when normalizing scores. Pairwise A/B/tie verdicts remain categorical.

## MCP fixtures

Put shared schemas at
`submissions/<pack>/mcp-servers/<server>/schema.json` and per-case fixtures at
`cases/<case-id>/mock-mcps/<server>/fixture.json`. Fixtures use
`{"sequence": [...]}`. The pack's `mcps.json` supplies MCP configuration.

`swap_mock_mcp.py` rewrites matching servers to launch the trial image's
`/opt/mock-mcp/src/server.py` over stdio. Enrichment uploads the matching schema
and fixture into each trial's workspace. Pairwise control configs can use a
generated `<pack>-mcp-only` sibling to expose the same tools without a skill.

When `inputs.tools` is configured, each entry needs a nonempty `match` and the
submission needs `tool_handlers.yaml`. `permissions.allow` and
`permissions.deny`, when present, are lists.

The local `submissions/aeh-hello-world`, `submissions/aeh-pairwise-example`, and
`examples/aeh-skill-konflux` directories demonstrate the retained AEH formats.
