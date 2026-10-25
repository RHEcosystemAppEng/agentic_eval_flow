# Konflux shared task contract

The workflow definition is
`konflux-shared-tasks/task/aeh-skill-eval/aeh-skill-eval.yaml`. This repository
provides its images and Python entry points. The caller supplies a trusted source
artifact and one `submission-dir` per matrix leg, such as `rh-sre/cve-impact`.

Each leg works under `source/legs/<slug>/`. It copies the skill pack, resolves
its local symlinks, and stages an optional
`submissions/<pack>/<skill>/metadata.yaml` submission. Cross-step state lives in
that leg's `.baked-vars/` directory. Reports use metadata's `name` when validation
ran, otherwise the `submission-dir` value. Every scanner, scorecard, and storage
step uses this same report key.

## Gates

Tier A always runs source preparation, skill structure validation, Cisco
scanning, and harness-eval SKILL.md scanning. Skill structure validation checks
`<pack>/skills/<skill>/SKILL.md` and its directory. Errors fail Tier A; warnings
remain advisory. It writes `skill-structure-validation.json`, which supplies
the `valid_skill_structure` certification check and the standalone
`<fact_ref_prefix>valid-skill-structure` Compass fact. Cisco scanning disables
LLM analysis and fails on high severity findings. This scan policy applies
uniformly to every skill.

Tier B runs validation, advisory quality review, and AEH only when
`should-evaluate=true`, the skill belongs to `changed-skills`, and an AEH
submission exists. The submission's `aeh_mode`, `aeh_control_config`, and
`aeh_treatment_config` take precedence over task defaults. Quality review
records findings and token usage without blocking evaluation.

`enable-persistence=true` independently enables scorecard/Compass Facts, OCI
report attachment, and Postgres/MinIO storage. Scan-only legs can persist their
Tier A findings. Security or advisory-review findings do not suppress
persistence. Source, validation, or evaluation runtime failures cause dependent
steps to skip.

The task emits `TEST_OUTPUT_TIER_A` and `TEST_OUTPUT_TIER_B` separately. Tier A
is the hard scan gate; Tier B emits `SKIPPED` when evaluation does not apply.
The shared `aggregate-fanout-result` task aggregates these across matrix legs.
Steps use `onError: continue` so a failed skill does not interrupt siblings;
the final result records runtime failures.

## Images and entry points

| Step | Image parameter | Repository entry point |
|---|---|---|
| materialize-source, compute-flags | `task-image` | Inline source/flag handling |
| swap-mock-mcp | `task-image` | `scripts/swap_mock_mcp.py` |
| validate | `task-image` | `scripts/validate.py` |
| skill-structure-validation | `task-image` | `scripts/validate_skill_structure.py` |
| security-scan | `security-scan-image` | `skill-scanner` |
| skillmd-scan | `skillmd-scan-image` | `harness-eval` |
| quality-review | `task-image` | `scripts/test_quality_review.py`, `scripts/write_metrics_checkpoint.py` |
| run-eval | `runner-image` | `scripts/run_aeh.py`, `scripts/aggregate_aeh.py` |
| trial pod | `aeh-image` | Upstream AEH reward verifier and agent CLI |
| analyze-and-scorecard | `task-image` | `scripts/analyze.py`, `scripts/aggregate_scorecard.py` |
| package-oci-reports, emit-results | `task-image` | Inline serialization |
| store | `task-image` | `scripts/store_results.py`, `scripts/publish.py` |

AEH uses Harbor's Kubernetes backend. The OpenShift environment adds writable
mounts, and task enrichment stages skills, annotations, tool handlers, and
per-case MCP fixtures before Harbor uploads each task to its trial pod.

An optional kubeconfig secret selects a remote workload cluster. Otherwise AEH
uses in-cluster credentials. The workload namespace needs pod creation,
inspection, deletion, and exec permissions; LLM credentials must be available
in the workload cluster under `llm-credentials`. The former
`workload-credentials-secret` task input was unused and has been removed.
