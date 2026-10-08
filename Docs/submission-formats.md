# Submission Formats

## Skill Submission (Harbor)

For full agent evaluation with container isolation and A/B comparison:

```
my-skill/
├── instruction.md       # Task description (required, or generated from SKILL.md)
├── skills/
│   └── SKILL.md         # Skill definition (required)
├── tests/
│   ├── test_outputs.py  # Verification tests (required, or generated)
│   └── llm_judge.py     # LLM-based judge (optional)
├── docs/                # Reference documentation (optional)
├── supportive/          # Mock MCPs, data files (optional, <50MB)
└── metadata.yaml        # eval_engine: harbor (required)
```

## Skill Submission (ASE)

For lightweight LLM-as-judge evaluation without containers:

```
my-skill/
├── skills/
│   └── SKILL.md         # Skill definition (required)
├── evals/
│   ├── evals.json       # Evaluation prompts and assertions (optional, generated if missing)
│   └── files/           # Test data files (optional)
└── metadata.yaml        # eval_engine: ase (required)
```

## MCP Server Submission

For validating MCP server implementations:

```
my-mcp-server-eval/
├── metadata.yaml        # eval_engine: mcpchecker (required)
├── mcp-config.yaml      # MCP server connection settings (required)
│                        #   - url: MCP server endpoint
│                        #   - auth: authentication config (if needed)
└── tasks/
    ├── task-1.yaml      # Task definition with expected tool calls
    └── task-2.yaml      # Each task tests specific MCP functionality
```

MCPChecker validates that the MCP server correctly handles tool invocations and returns expected results.

## Agent Submission (A2A Protocol)

For evaluating agents that implement the A2A (Agent-to-Agent) protocol:

```
my-a2a-agent-eval/
├── metadata.yaml        # eval_engine: a2a (required)
├── agent-config.yaml    # Agent endpoint and auth config (required)
│                        #   - endpoint: http://agent-service:8000
│                        #   - auth: bearer token or API key
└── tasks/
    ├── instruction.md   # Task description
    ├── tests/
    │   └── test_outputs.py
    └── task.toml        # Task configuration
```

A2A evaluation connects to a deployed agent via the A2A protocol and runs evaluation tasks against it.

## Agent Submission (Harbor)

For evaluating general agents (non-A2A) with full container isolation:

```
my-agent-eval/
├── metadata.yaml        # eval_engine: harbor, persona: agent (required)
├── instruction.md       # Task description (required)
├── tests/
│   ├── test_outputs.py  # Verification tests (required)
│   └── llm_judge.py     # LLM-based judge (optional)
└── supportive/          # Environment files, data (optional)
```

Harbor creates treatment/control container variants and runs A/B comparison.

## AEH Submission (Agent-Eval-Harness)

For evaluating agents using the Agent-Eval-Harness framework with flexible LLM judges.
Trials run as Harbor jobs on OpenShift.

**Single** (`aeh-mode=single`):

```
my-aeh-eval/
├── metadata.yaml        # eval_engine: aeh (required)
├── eval.yaml            # AEH config (models, judges, thresholds, outputs)
├── skills/…/SKILL.md    # Optional skill package
└── cases/
    └── case-001/
        ├── input.yaml
        └── mock-mcps/           # Optional, see "Mock MCP servers" below
            └── <server-name>/
                └── fixture.json
```

**Pairwise** (`aeh-mode=pairwise`):

```
my-aeh-pairwise/
├── metadata.yaml
├── eval-control.yaml    # Baseline (often unskilled)
├── eval-treatment.yaml  # Treatment + pairwise LLM judge
├── skills/<name>/SKILL.md
└── cases/
    └── case-001/
        ├── input.yaml
        └── mock-mcps/
            └── <server-name>/
                └── fixture.json
```

### Mock MCP servers (AEH submissions)

For a skill whose `eval.yaml` (`runner.plugin_dirs`) points at a pack that
declares real MCP servers in its own `mcps.json` (e.g. `rh-sre/mcps.json`'s
`lightspeed-mcp`), evaluation can swap any of those server names for a
config-driven mock instead of calling the real, credentialed server:

```
submissions/<plugin>/
├── mcp-servers/                     # Shared across every skill under this plugin
│   └── <server-name>/
│       └── schema.json              # Tool contract: same for every case
├── <skill-a>/
│   ├── eval.yaml
│   └── cases/
│       ├── case-001/
│       │   ├── input.yaml
│       │   └── mock-mcps/
│       │       └── <server-name>/
│       │           └── fixture.json # THIS case's tool-call sequence
│       └── case-002/
│           └── input.yaml           # No mock-mcps/ -- this case needs no MCP mock
└── <skill-b>/
    ├── eval.yaml
    └── cases/
        └── case-001/
            ├── input.yaml
            └── mock-mcps/
                └── <server-name>/
                    └── fixture.json # A different sequence, same shared schema.json
```

- `mcp-servers/<server-name>/schema.json` is **plugin-shared**: one tool
  contract (`name`, `description`, `inputSchema`, `outputSchema`,
  `outputExample` per tool) reused by every case of every skill submitted
  under that plugin. Lives alongside the plugin's skill submission dirs
  (`submissions/<plugin>/mcp-servers/...`), not inside the real
  `<plugin>/` source tree.
- `cases/<case>/mock-mcps/<server-name>/fixture.json` is **per-case**:
  `{"sequence": [{"tool": "...", "input": {...}, "output": {...}}, ...]}` --
  the exact ordered tool-call responses that one case's trial should see.
  A case with no `mock-mcps/` folder needs no MCP mock at all.
- A skill is auto-opted into mocking a server name the moment any of its
  cases defines `mock-mcps/<that-name>/fixture.json` -- no separate flag,
  deploy step, or image to configure. The mock server (generic-mock-mcp-
  server, pinned into the shared trial base image) runs as a plain stdio
  subprocess per trial pod; see
  `abevalflow.harbor_extensions.aeh_task_enrichment`'s docstring for the
  full mechanism.

Verified smoke samples in [skill-submissions](https://github.com/RHEcosystemAppEng/skill-submissions):
- Branch `eval/aeh-hello-world-single` -> `aeh-hello-world-single`
- Branch `eval/aeh-hello-world-pairwise` -> `aeh-hello-world-pairwise`

Working trigger defaults: LiteLLM `claude-sonnet` via
`http://litellm.ab-eval-flow.svc.cluster.local:4000`, AEH image
`quay.io/ecosystem-appeng/agent-eval-harness:v1.0.3`.

MinIO layout for AEH: `debug/harbor/` (raw Harbor jobs) + `debug/aeh/`
(`summary.yaml`, `report.html`, `cases/`). Pairwise HTML is regenerated with
`--baseline` so treatment `report.html` includes the pairwise section.

See [Trigger Guide](trigger_guide.md) for full YAML examples, or run:

```bash
./scripts/misc/trigger_test_runs.sh                 # all engines including AEH
./scripts/misc/trigger_test_runs.sh "$(git branch --show-current)" aeh   # AEH only
```
