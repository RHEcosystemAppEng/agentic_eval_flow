# Application architecture

`abevalflow` is a Python application with a CLI boundary. Business operations are
importable services; shell launchers do not contain application logic.

| Package | Responsibility |
|---|---|
| `cli` | Arguments, terminal output, exit codes, compatibility command interfaces |
| `config` | Submission loading, environment aliases, explicit overrides, packaged policies |
| `workflow` | Plans, dependencies, isolated workspaces, execution, finalization |
| `validation` | Submission checks and scanner execution |
| `review` | Configured skill and submission review services |
| `execution` | Backend contracts, Harbor adapter, remote cluster environment, artifact collection |
| `results` | Versioned run evidence, normalized AEH results, atomic JSON writes |
| `policy`, `certification`, `scorecard` | Gate aggregation and certification decisions |
| `reporting` | Markdown, certification JSON, and diagnostic presentation |
| `persistence` | Explicit PostgreSQL, S3, Compass, and OCI publication |
| `harbor_extensions` | OpenShift environment and AEH task enrichment |
| `observability` | Tracing, timing, token usage, costs, and checkpoints |
| `_vendor/aeh` | Unmodified scoring/reporting resources from the pinned upstream release |

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

## Dependency boundaries

Package services do not import `scripts`. Compatibility launchers import command
interfaces, which call package services. CLI-only argument parsing remains under
`cli`. Environment resolution is concentrated in `config`; the remote runner
receives an explicit child environment. Review services receive explicit client
configuration. Pure scoring and policy functions operate without network access.

The upstream adapter supplies packaged scorer/reporter resources and forwards
pack MCP configurations in its child process. It does not rewrite files inside
the installed upstream dependency. AEH and trial images must use compatible
versions; the supported AEH revision is pinned in `pyproject.toml` and the trial
Containerfile.

## Validation

Tests cover submission contracts, scoring, certification, scanner protocol,
configuration precedence, source isolation, workflow progression, remote-cluster
configuration, persistence retries, and CLI behavior. Live model and workload
cluster runs require deployment credentials and are separate from the default
unit/integration fixture suite. `make test` enforces coverage, lint, and format
checks. Wheel installation must also work outside the source checkout with only
the selected optional dependencies.
