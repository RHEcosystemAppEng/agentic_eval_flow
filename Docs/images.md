# Image builds and releases

Run builds with the repository root as context. `make images` builds all five
images; individual targets such as `make aeh-trial` are also available.
`CONTAINER_ENGINE`, `IMAGE_REPOSITORY`, `VERSION`, and `PLATFORM` override
Podman, the shared image repository, the tag prefix, and the target platform.
The default platform is `linux/amd64`, matching the original deployed image.

```sh
make images IMAGE_REPOSITORY=quay.io/ecosystem-appeng/agent-eval-harness VERSION=v3.0
# Publish after validating the images and choosing the release registry.
for purpose in runner task trial security-scan skillmd-scan; do
  podman push "quay.io/ecosystem-appeng/agent-eval-harness:v3.0-$purpose"
done
```

Build individual images with their Make targets, such as
`make aeh-task VERSION=v4.0.2`. Publish the resulting tag, then update the shared
task's image reference with its registry digest and publish a new task bundle.

The shared task selects these image tags:

| Purpose | Tag |
|---|---|
| Task utilities | `v4.0.2-task` |
| Cisco scanner | `v4.0.1-security-scan` |
| SKILL.md scanner | `v4.0-skillmd-scan` |
| Orchestrator | `v4.0-runner` |
| Trial runtime | `v4.0-trial` |

Callers overriding `aeh-image` or `AEH_IMAGE` select the `-trial` image;
`runner-image` selects `-runner`. Image references are pinned by digest in the
shared task.

## Dependency ownership

The task and runner images export their selected dependencies from `uv.lock`,
install those exact versions, then install the application package. The task
image includes model-review, storage, and OTLP telemetry support.
The scanner images install only their pinned scanner packages. The orchestrator
installs the application with its `runner`, `llm`, and telemetry extras, including
Harbor and the Kubernetes client. It separately installs the complete pinned
AEH source distribution unchanged. The trial
image contains upstream AEH's reward/scoring code, Claude Code, and the mock MCP
server. It receives case files and skills at runtime through Harbor.

The Cisco scanner image applies a guarded build-time compatibility patch to
version `2.0.11`: `allowed-tools` accepts the Agent Skills specification's
space-separated string as well as comma-separated strings and existing YAML
lists. Spaces and commas inside scoped tool arguments are preserved. Image
builds exercise the installed parser and CLI, including a negative case that
must still fail the HIGH gate when Bash is undeclared. Review the patch when
upgrading Cisco; its version and source checks deliberately fail on drift.

Both AEH images install the complete AEH release `v1.57.1`, pinned to commit
`8a15a8b687b5561903827805eaae8b438bde9954`. The local runtime installer uses
the same pin from `abevalflow/execution/runtime.py`. Keep all three pins aligned.
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

GitHub CI runs the Python checks and builds all five Containerfiles. The existing
Konflux `.tekton/agent-eval-harness-{push,pull-request}.yaml` definitions build
the orchestrator component using the package's AEH pin. Additional
Konflux image components can point at the other four Containerfiles with the
same repository-root build context. Component registrations and registry
credentials belong to the deployment environment.
