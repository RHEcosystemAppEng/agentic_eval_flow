# Application architecture

`abevalflow` is a Python application with a CLI boundary. Business operations are
importable services; shell launchers do not contain application logic.

| Package | Responsibility |
|---|---|
| `cli` | Arguments, terminal output, and exit codes |
| `config` | Submission loading, environment aliases, explicit overrides, packaged policies |
| `workflow` | Plans, dependencies, isolated workspaces, execution, finalization |
| `validation` | Submission checks and scanner execution |
| `review` | Configured skill and submission review services |
| `execution` | Backend contracts, Harbor adapter, remote cluster environment, artifact collection |
| `results` | Versioned run evidence, normalized AEH results, atomic JSON writes |
| `certification`, `scorecard` | Gate aggregation and certification decisions |
| `reporting` | Markdown, scorecards, and certification JSON |
| `persistence` | Explicit PostgreSQL, S3, and Compass publication |
| `harbor_extensions` | Application-owned Harbor task inputs and MCP fixture preparation |
| `observability` | Tracing, timing, token usage, costs, and checkpoints |

## Workflow contracts

`RuntimeConfig` resolves one invocation, with credentials excluded from its
serialized representation. `RunResult` records the source revision, evaluator
version, configuration, steps, and publication outcomes. Each `StepResult`
separates execution status from policy verdict and includes artifact references.

`build_plan` is side-effect free. `Operations` supplies the implementations.
`execute_step` applies prerequisites and records outcomes. Combined runs and
container phases call the same operations. Source preparation uses a private
copy; MCP rewrites and generated configurations do not modify authored inputs.

Report generation is independent of publication. External clients are loaded
only for requested capabilities. PostgreSQL writes use a transaction per snapshot;
S3 uses stable run prefixes. Publication destinations do not depend on each other.
The consuming pipeline converts `run.json` into CI results and owns OCI evidence
attachment; no Konflux-specific commands or registry clients are packaged.

## Dependency boundaries

The packaged `abevalflow/config/certification_profiles.yaml` is the source for
certification profiles.

Tier 1 scanners are pinned base dependencies, installed unchanged alongside the
CLI. Default local execution resolves their entrypoints from the CLI's Python
environment; it also works with isolated tool installs that expose only `abeval`.
Explicit command overrides and Podman/Docker execution are optional alternatives.

The installed CLI calls package services; services do not import CLI modules. Argument parsing remains under `cli`. Environment resolution is concentrated in `config`; the remote runner
receives an explicit child environment. Review services receive explicit client
configuration and share the completion/token-accounting service. Pure scoring and policy functions operate without network access.

AEH runs in an external, complete source installation. The runtime installer
pins the upstream revision and installs it unchanged in an isolated environment.
The runner invokes upstream commands in subprocesses using that environment's
Python interpreter. No AEH implementation is included in the application wheel.

Task preparation supplies native Harbor `environment.mcp_servers` declarations
for stdio mocks, per-case schemas and fixture files, skill projections, and
verifier-only annotations. The agent starts each mock server as its own stdio
subprocess. Each trial and control/treatment arm receives separate files and
process state. Only fixture-backed servers are registered; other pack servers
are excluded. Generated verifier scripts are retained byte-for-byte.
AEH's native Kubernetes environment owns pod lifecycle and transfers. Runner and
trial images use the same AEH revision. Model Secrets use native SDK variable
names, and certificate trust uses normal SDK/OS configuration.

## Validation

Tests cover submission contracts, scoring, certification, scanner protocol,
configuration precedence, source isolation, workflow progression, remote-cluster
configuration, persistence retries, and CLI behavior. Live model and workload
cluster runs require deployment credentials and are separate from the default
unit/integration fixture suite. `make test` enforces coverage, lint, and format
checks. Wheel installation must also work outside the source checkout with only
the selected optional dependencies.

Native upstream task generation and Harbor schema validation can be exercised
without model access:

```sh
abeval runtime install --directory /tmp/aeh-runtime
ABEVAL_TEST_AEH_ROOT=/tmp/aeh-runtime/source uv run --extra dev pytest tests/test_native_aeh.py -q
```

After building the trial image, set `ABEVAL_TEST_TRIAL_IMAGE` to its local tag
to include actual stdio tool calls and deterministic AEH verification inside
network-isolated containers. These tests exercise two cases in both control and
treatment, including fresh mock-server processes and Harbor's Claude MCP
registration command. They require Podman but no model credentials or cluster.
