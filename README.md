# Agentic Eval Flow

Automated Tekton-orchestrated pipeline on OpenShift for evaluating AI artifacts:

- **Skills** -- Measures skill efficacy by comparing agent performance with and without skills (A/B "gap" testing)
- **MCP Servers** -- Validates MCP server implementations via task-based verification
- **Agents** -- Evaluates full agent behavior using Harbor (general agents) or A2A protocol (A2A-compliant agents)

Produces statistical reports with pass rates, uplift metrics, significance tests, and a unified scorecard.

## Pipelines

| Pipeline | Purpose | Key Differences |
|----------|---------|-----------------|
| **CI Pipeline** | Full evaluation for new submissions | Includes security scan, quality review, artifact generation |
| **Monitoring Pipeline** | Regression detection for deployed artifacts | Includes degradation check against historical baseline, Slack alerts |

## How It Works

The pipeline executes in six main stages, with engine-specific steps within each:

### 1. Prepare
- Clone submission repository
- Validate structure and `metadata.yaml` schema
- AI-assisted generation of missing test artifacts (optional)

### 2. Test (CI Pipeline only)
- **Quality Review** -- AI-powered review of skill/test coherence (advisory)
- **Security Scan** -- [Cisco AI Defense](https://github.com/cisco-ai-defense/skill-scanner) scan for prompt injection, data exfiltration risks
- **Security & Quality Scan** -- [harness-eval](https://github.com/redhat-community-ai-tools/harness-eval) deterministic scan (27 rule categories covering prompt injection, credential access, obfuscation, coercive overrides, stealth persistence, data exfiltration, description quality, broken references, and more)

### 3. Evaluate

Six evaluation engines, each suited for different artifact types:

| Engine | Evaluates | Comparison Mode | Container Isolation |
|--------|-----------|-----------------|---------------------|
| **Harbor** | Skills, general agents | A/B (treatment vs control) | Yes |
| **ASE** | Skills only | A/B (treatment vs control) | No |
| **A2A** | A2A-protocol agents | A/B (treatment vs control) | Yes |
| **MCPChecker** | MCP servers | Single-agent task verification | No |
| **AEH** | Agents, skills | Judge-based evaluation | Yes (K8s Harbor pods) |
| **AEH OpenShell OpenClaw** | OpenClaw agents via forge-saw | Judge-based (AEH scoring) | Yes (SAW VM + inner sandbox) |

Engines are implemented in `abevalflow/engines/` using a registry pattern.

### 4. Analyze
- Compute pass rates, uplift (gap), statistical significance (p-value)
- Generate `report.json` and `report.md`
- Aggregate gate results into unified `scorecard.json`
- **Monitoring only:** Check for degradation against historical baseline

### 5. Store
- Upload reports and artifacts to MinIO
- Record results to PostgreSQL for historical analysis

### 6. Cleanup
- Remove temporary workspaces and artifacts

## Configuration

All flow configuration is defined in `metadata.yaml` within each submission:

```yaml
name: my-submission
eval_engine: harbor              # harbor, ase, a2a, mcpchecker, aeh, or aeh_openshell_openclaw
persona: general                 # Agent persona for Harbor/A2A

experiment:
  n_trials: 20                   # Number of evaluation attempts

gate_policy:
  default_mode: warn
  combination: all_pass
  gates:
    evaluation:
      mode: block
      threshold: 0.0
    security:
      mode: warn
```

See [Gate Policy Configuration](Docs/gates-architecture.md#gate-policy-configuration) for full options.

## Repository Structure

```
agentic_eval_flow/
├── Docs/                    # ADR, implementation plan, guides
├── pipeline/
│   ├── pipeline.yaml        # Main pipeline definition
│   ├── triggers/            # EventListener, TriggerTemplate, TriggerBinding
│   └── tasks/               # Tekton task definitions (phases, components, post)
├── templates/               # Jinja2 templates (Dockerfiles, test.sh, task.toml)
├── scripts/                 # Python scripts invoked by pipeline tasks
├── config/                  # K8s manifests (RBAC, PostgreSQL, LiteLLM)
└── tests/                   # Unit and integration tests
```

## Related Repositories

| Repository | Purpose |
|---|---|
| [skill-submissions](https://github.com/RHEcosystemAppEng/skill-submissions) | Submission intake -- users push skills, MCP evals, and agent evals here |
| [laude-institute/harbor](https://github.com/laude-institute/harbor) | Harbor upstream (PyPI `harbor==0.20.0`) -- classic A/B uses stock Harbor + OpenShift custom env |
| [opendatahub-io/agent-eval-harness](https://github.com/opendatahub-io/agent-eval-harness) | AEH -- KubernetesEnvironment base for the OpenShift custom env plugin |
| [cisco-ai-defense/skill-scanner](https://github.com/cisco-ai-defense/skill-scanner) | Security scanner for prompt injection and data exfiltration detection |
| [harness-eval](https://github.com/redhat-community-ai-tools/harness-eval) | Deterministic security and quality scanner for skill submissions (27 rule categories, 97 rules) |

## LLM Access

The pipeline is LLM-agnostic. Three modes are supported:

| Mode | Proxy Required? |
|---|---|
| Direct API key (Anthropic, OpenAI, etc.) | No |
| opencode + self-hosted model (vLLM, Ollama) | No |
| Google Vertex AI + LiteLLM proxy | Yes |

## Prerequisites

- OpenShift cluster with Pipelines operator (Tekton)
- Container registry (Quay.io) with push credentials
- Stock Harbor (`harbor==0.20.0`) with the OpenShift custom environment plugin
- LLM access (one of the three modes above)
- Python 3.11+

## Documentation

- [Trigger Guide](Docs/trigger_guide.md) -- How to submit skills, configure gate policies, and interpret scorecard results
- [Gates Architecture](Docs/gates-architecture.md) -- Gate types, modes, GateResult schema, scorecard, and gate policy configuration
- [Submission Formats](Docs/submission-formats.md) -- Directory layouts for skill, agent, MCP, and AEH submissions
- [Extensibility](Docs/extensibility.md) -- How to add new engines, security gates, quality gates, and gate categories
- [Persistence](Docs/persistence.md) -- MinIO object storage layout and PostgreSQL results database
- [Compass Integration](Docs/compass_facts_integration.md) -- Pushing gate results to Red Hat Compass
- [ADR: Skill Evaluation Pipeline](Docs/ADR_Skill_Evaluation_Pipeline_and_Harbor_Execution_Strategy.txt)

## License

Apache License 2.0
