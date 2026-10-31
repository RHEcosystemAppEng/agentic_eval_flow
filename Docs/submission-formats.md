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

Optional `plugin_name` records the owning plugin. It must match the plugin in
`<plugin>/skills/<skill>` when evaluating a catalog path; standalone skills can
supply it through metadata or `--plugin-name`.

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

## Writing judges and deterministic checks

An AEH judge with `type: check` is an inline **Python** snippet. It receives an
`outputs` mapping containing captured artifacts and tool calls, plus an
`arguments` mapping for any judge-specific configuration. Return a two-item
tuple: `(passed: bool, explanation: str)`. Keep checks focused on observable
contracts such as required report sections, exact tool arguments, call counts,
and approval boundaries. Use `type: llm` when correctness needs interpretation
of prose or evidence rather than an exact mechanical rule.

For example, this check verifies a report file and stable headings:

```yaml
judges:
  - name: report_completeness
    type: check
    check: |
      files = outputs.get("files") or {}
      report = files.get("output/result.md", "")
      required = ["## Findings", "## Evidence", "## Limitations"]
      missing = [heading for heading in required if heading not in report]
      return not missing, "missing headings: " + (", ".join(missing) if missing else "none")
```

Use the same headings in the case prompt, check, and any output template.
Annotations are grader-only ground truth; use them to describe expected tool
calls, evidence, and outcomes. The agent should receive the task in
`input.yaml`, not the answer key. Tool fixtures live under that case's
`mock-mcps/<server>/fixture.json`; declare the expected call sequence in the
annotations and check actual calls against it when order or arguments matter.

For numeric LLM judges, state the score anchors in the prompt and set
`feedback_type: float` with `score_range: [0, 1]`. A `reward` block can combine
normalized judge values using named weights; the weights should sum to 1.0 and
each judge should measure a distinct part of quality. Set hard workflow
requirements as checks with `min_pass_rate: 1.0`, rather than letting a strong
prose score hide an unauthorized or missing tool call. Keep the pairwise judge
separate: it compares treatment and control, while weighted reward measures
the treatment's own case quality.

Validate a submission from the `agentic_eval_flow` repository:

```sh
abeval validate submission submissions/<pack>/<skill>
```

## MCP fixtures

Put shared schemas at
`submissions/<pack>/mcp-servers/<server>/schema.json` and per-case fixtures at
`cases/<case-id>/mock-mcps/<server>/fixture.json`. Fixtures use
`{"sequence": [...]}`. The pack's `mcps.json` supplies MCP configuration.

The MCP preparation service resolves matching servers to the trial image's
`/opt/mock-mcp/src/server.py` executable and declares them through Harbor's native
`environment.mcp_servers` task input. The selected agent registers and launches
the executable as a stdio server. Preparation stages each schema and case fixture
in that trial's private workspace. Pairwise control configs can use a
generated `<pack>-mcp-only` sibling to expose the same tools without a skill. Missing fixtures for a server used by other cases
receive an empty sequence, retaining the mock server's existing schema fallback.
Evaluation trials register only fixture-backed stdio mock servers. Other servers
from the pack are excluded from both control and treatment. A submission without
MCP fixtures registers no MCP servers. Mock substitution removes the real
endpoint's authentication fields because the fixture server does not use them.

Annotations and referenced ground-truth files are staged under `tests/cases/`
for the verifier and are not copied into the agent workspace. Verifier scripts
remain the upstream-generated files.

When `inputs.tools` is configured, each entry needs a nonempty `match` and the
submission needs `tool_handlers.yaml`. `permissions.allow` and
`permissions.deny`, when present, are lists.

The checked-in `submissions/aeh-pairwise-example` demonstrates the pairwise
submission format. Validate it with:

```sh
abeval validate submission submissions/aeh-pairwise-example
```
