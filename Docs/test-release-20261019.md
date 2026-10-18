# AEH split-image test release — 19 October 2026

Release prefix: `v3.0-test-20261019-021235`. All images use `linux/amd64` and
share `quay.io/ecosystem-appeng/agent-eval-harness`.

| Purpose | Shared task parameter | Published image |
|---|---|---|
| runner | `runner-image` | `quay.io/ecosystem-appeng/agent-eval-harness:v3.0-test-20261019-021235-runner@sha256:a08f7fbbfcd6209add56c946022697412809bbf43010e4536aef1d6a9f66d34c` |
| task | `task-image` | `quay.io/ecosystem-appeng/agent-eval-harness:v3.0-test-20261019-021235-task@sha256:569cdd8432aaaafef52f5bd7d9f0a05eab0ab618e37e1e35d7b828aa83d738be` |
| trial | `aeh-image` | `quay.io/ecosystem-appeng/agent-eval-harness:v3.0-test-20261019-021235-trial@sha256:4f2abcd472a19d76a052bdb48ed38a0f4abd175127a1b44af1bc30e6c1fa19da` |
| security-scan | `security-scan-image` | `quay.io/ecosystem-appeng/agent-eval-harness:v3.0-test-20261019-021235-security-scan@sha256:cca56cd0f451bda9a2f1d047ad0f636b6d450a4b5b0f63a208d83b97598e9941` |
| skillmd-scan | `skillmd-scan-image` | `quay.io/ecosystem-appeng/agent-eval-harness:v3.0-test-20261019-021235-skillmd-scan@sha256:5c35e22bfb5709941d93a88f1f0ecf4393779a9b77204be8ca26132d78311ad9` |

## Published task bundle

`quay.io/ecosystem-appeng/konflux-skill-evaluation/aeh-skill-eval:0.7.0@sha256:1cfbd5ba59ba9cd8857e730943a0578bc8bcc4938cbde301abf5d77ca0eaccda`

`make bundles` published all five shared task bundles. The four supporting
bundles retain their prior digests. The published AEH Task was pulled back by
digest and checked against the local Task, allowing only Tekton's empty
`computeResources` defaults.

## Consumer wiring

Both `.tekton/skills-evaluation-push.yaml` and
`.tekton/skills-evaluation-pull-request.yaml` in `agentic-plugins` pin the new
AEH bundle. The push pipeline's `AEH_IMAGE` default selects the published trial
image above; the unused workload-credentials parameter and argument are removed.

## Verification

- Built and pushed all five images; registry digests verified.
- 546 Python tests passed; lint and formatting passed.
- Shared-task YAML, shell syntax, and caller argument compatibility checked.
- No cluster PipelineRun or LLM evaluation was started.

Source changes remain uncommitted in the three existing working branches.
