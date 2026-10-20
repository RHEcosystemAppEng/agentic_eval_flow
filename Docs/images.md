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

The shared task currently selects the published, digest-pinned test images
listed in [the test release manifest](test-release-20261019.md). They all share
`quay.io/ecosystem-appeng/agent-eval-harness` with the prefix
`v3.0-test-20261019-021235` and one of the suffixes `-runner`, `-task`, `-trial`,
`-security-scan`, or `-skillmd-scan`.

Build and publish all five images before updating the task bundle for a future
release. Callers overriding `aeh-image` or `AEH_IMAGE` select the `-trial` image;
`runner-image` selects `-runner`. Remove the unused `workload-credentials-secret`
argument from callers when upgrading the shared task.

## Dependency ownership

The task image installs this project's dependencies plus OTLP telemetry support.
The scanner images install only their pinned scanner packages. The orchestrator
contains Harbor, the Kubernetes client, upstream AEH, and the three scripts
needed to run/aggregate evaluations and rewrite MCP configuration. The trial
image contains upstream AEH's reward/scoring code, Claude Code, and the mock MCP
server. It receives case files and skills at runtime through Harbor.

The Cisco scanner image applies a guarded build-time compatibility patch to
version `2.0.11`: `allowed-tools` accepts the Agent Skills specification's
space-separated string as well as comma-separated strings and existing YAML
lists. Spaces and commas inside scoped tool arguments are preserved. Image
builds exercise the installed parser and CLI, including a negative case that
must still fail the HIGH gate when Bash is undeclared. Review the patch when
upgrading Cisco; its version and source checks deliberately fail on drift.

Both AEH images use AEH release `v1.53.3`, pinned to commit
`7ec9078b621d8aa5d2c0ea3b4e61f9e60b4bed2f`. Their `AEH_SHA` build arguments
require a full commit SHA. Update both together.
The mock server uses commit `f68734ac68e504f4856c7a1d25e01919b95b3bb7`;
`MOCK_SHA` also requires a full commit SHA. Claude Code is pinned through
`CLAUDE_CODE_VERSION` in the trial Containerfile.

The runner applies one small build-time patch to forward pack `mcps.json` files
to Harbor because upstream AEH does not yet do this. The patch fails when its
expected source changes. Pairwise judge warning handling is included in this
AEH release and no longer needs a local patch. AEH v1.53.3 also changes score
validation and reward normalization; treat results from before and after this
upgrade as separate baselines.

All runtime images use an unprivileged user and writable group-0 directories
for OpenShift arbitrary UIDs. Task dependencies are installed during the build;
steps do not install Python packages during evaluation.

## CI

GitHub CI runs the Python checks and builds all five Containerfiles. The existing
Konflux `.tekton/agent-eval-harness-{push,pull-request}.yaml` definitions build
the orchestrator component using the Containerfile's AEH pin. Additional
Konflux image components can point at the other four Containerfiles with the
same repository-root build context. Component registrations and registry
credentials belong to the deployment environment.
