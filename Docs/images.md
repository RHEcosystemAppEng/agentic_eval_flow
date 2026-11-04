# Image builds and releases

Run builds with the repository root as context. `make images` builds both
images; individual targets such as `make aeh-trial` are also available.
`CONTAINER_ENGINE`, `IMAGE_REPOSITORY`, `VERSION`, and `PLATFORM` override
Podman, the shared image repository, the tag prefix, and the target platform.
The default platform is `linux/amd64`.

```sh
make agent-eval-harness
make push-agent-eval-harness

# Build and push both images when both runtimes need changes.
make images IMAGE_REPOSITORY=quay.io/example/agent-eval-harness VERSION=v1.0.6
make push IMAGE_REPOSITORY=quay.io/example/agent-eval-harness VERSION=v1.0.6
```

| Build target | Tag suffix | Contents |
|---|---|---|
| `agent-eval-harness` | `runner` | Complete evaluation CLI with included scanners, storage/model clients, and unchanged AEH. |
| `aeh-trial` | `trial` | Claude Code, native AEH verifier, and stdio mock MCP executable. |

Resulting tags use `<repository>:<version>-<suffix>`. `make push-agent-eval-harness`
pushes the CLI image; `make push-aeh-trial` pushes the trial image. Push the images you need
and configure consumers with immutable digests. The local CLI selects the trial
image with `--trial-image`. For local Podman/Docker scanning, set
`--security-image` and `--quality-image` to your published evaluation image;
both flags can use the same reference. No scanner image is selected implicitly.
Deployed Konflux image choices are owned by the shared task.

Release `v1.0.6-runner` contains CLI `0.5.1` with the consolidated HTML report.
It uses the existing `v1.0.2-trial` image.

Release `v1.0.5-runner` adds explicit score breakdowns, absolute treatment reward
gates, and Compass publishing through the existing opt-in TLS verification bypass.
It uses the existing `v1.0.2-trial` image.

Release `v1.0.4-runner` contains CLI `0.5.0`, including PostgreSQL evidence schema
v2, plugin summaries, aspect-level scores with component drill-down, and uploaded
report references. It retains Harbor `0.25.0`, sequential batches, and explicit
TLS configuration. Use it with the existing `v1.0.2-trial`, which includes the
opt-in Python TLS hook for upstream judges. PostgreSQL publication requires a
fresh schema v2 database; see
[persistence](persistence.md). Skill loading, fixtures, and evaluation definitions
are unchanged.

## Dependency ownership

The evaluation image exports its scanner, runner, model, storage, and tracing
dependencies from `uv.lock`, installs those exact versions, then installs the CLI.
Harbor and the Kubernetes client orchestrate trials. Local installs and the
evaluation image use the same scanner packages. The complete pinned AEH source
distribution is installed unchanged.
The trial image contains upstream AEH's verifier/scoring code, Claude Code, and
the mock MCP server. Harbor transfers cases and skills into trial pods.

Cisco Skill Scanner `2.3.1` and harness-eval `7.9.0` are pinned application
dependencies. Both are installed unchanged. Cisco natively supports the standard
space-separated `allowed-tools` declaration. Scanner regression tests exercise
declared and undeclared tools through a complete local Tier 1 run.

Both AEH images install the complete AEH release `v1.57.1`, pinned to commit
`8a15a8b687b5561903827805eaae8b438bde9954`. The local runtime installer uses
the same pin from `abevalflow/execution/runtime.py`. Keep both pins aligned.
The application wheel contains no AEH implementation. Each image retains the
upstream checkout, its templates, skills, and license and installs it editable
without changing tracked source files.

The mock server uses commit `f68734ac68e504f4856c7a1d25e01919b95b3bb7`.
`MOCK_SHA` requires a full commit SHA. Claude Code is pinned through
`CLAUDE_CODE_VERSION` in the trial Containerfile.

MCP servers are declared through native Harbor task configuration. No loader,
subprocess, or HTTP-client monkeypatches are installed. Workload Secrets must
use SDK variable names, and internal endpoints must have trusted certificates.
See [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

All runtime images use an unprivileged user and writable group-0 directories
for OpenShift arbitrary UIDs. Task dependencies are installed during the build;
steps do not install Python packages during evaluation.

## CI

GitHub CI runs the Python checks and builds both Containerfiles. The existing
Konflux `.tekton/agent-eval-harness-{push,pull-request}.yaml` definitions build
the orchestrator component using the package's AEH pin. The trial component
uses its Containerfile with the same repository-root build context. Component
registrations and registry credentials belong to the deployment environment.
