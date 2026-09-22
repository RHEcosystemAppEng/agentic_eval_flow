# Pipeline Deployment Commands

Complete reference for deploying ABEvalFlow pipelines to OpenShift.

## Prerequisites

```bash
# Verify you're logged in and in the correct namespace
oc whoami
oc project ab-eval-flow
```

---

## Common Resources (All Pipelines)

### 1. RBAC

Create ServiceAccount and grant necessary permissions for pipeline execution.

```bash
# Apply RBAC (ServiceAccount, Role, RoleBindings)
oc apply -f config/rbac.yaml
```

### 2. ConfigMaps

Apply pipeline configuration and monitoring state.

```bash
# Apply pipeline defaults
oc apply -f config/pipeline_defaults.yaml

# Apply monitoring configmaps
oc apply -f pipeline/configmaps/
```

### 3. Secrets

Verify required secrets exist (create if missing).

```bash
# Check existing secrets
oc get secrets | grep -E "github-token|minio|db-credentials|litellm|llm-credentials"

# Required secrets (should already exist):
# - ab-eval-db-credentials (PostgreSQL)
# - minio-credentials (MinIO storage)

# Optional secrets (create if needed):
# - github-token (for PR comments)
# - llm-credentials (for LLM API access)
```

**Create missing secrets** (if needed):

```bash
# GitHub token (for PR integration)
oc apply -f config/github_token_secret_template.yaml  # Edit token first

# LLM credentials (if using direct API access)
oc create secret generic llm-credentials \
  --from-literal=api-key=<your-key>
```

### 4. Storage

Create PersistentVolumeClaims for pipeline workspaces.

```bash
# Create workspace PVCs
oc apply -f config/storage/workspace_pvc.yaml
oc apply -f config/storage/dead_letter_pvc.yaml
```

### 5. Pipeline Tasks

Apply all Tekton task definitions (components, phases, post-processing).

```bash
# Apply component tasks (git-clone, validate, scaffold, build, eval, scan, review)
oc apply -f pipeline/tasks/components/

# Apply phase tasks (prepare, test, evaluate)
oc apply -f pipeline/tasks/phases/

# Apply post-processing tasks (analyze, store, cleanup)
find pipeline/tasks/post -name "*.yaml" ! -name "*_deprecated*" -exec oc apply -f {} \;
```

### 6. Trigger Resources

Apply EventListener, TriggerTemplates, and TriggerBindings for webhook integration.

```bash
# Apply trigger templates and bindings
oc apply -f pipeline/triggers/trigger-template.yaml
oc apply -f pipeline/triggers/trigger-binding.yaml
oc apply -f pipeline/triggers/litellm-config-template.yaml
oc apply -f pipeline/triggers/litellm-config-binding.yaml
oc apply -f pipeline/triggers/quay-push-template.yaml
oc apply -f pipeline/triggers/quay-push-binding.yaml

# Apply event listener (creates HTTP endpoint for webhooks)
oc apply -f pipeline/triggers/event-listener.yaml
```

---

## Pipeline Definitions

### CI Pipeline (Production)

Full evaluation pipeline with security scan and quality review.

```bash
oc apply -f pipeline/pipelines/ci-pipeline.yaml
```

**Pipeline name**: `abevalflow-pipeline`

**Use for**: New skill/agent/MCP submissions requiring full validation

### CI Pipeline (Development)

Development variant of CI pipeline for testing changes.

```bash
oc apply -f pipeline/pipelines/ci-pipeline-dev.yaml
```

**Pipeline name**: `abevalflow-pipeline-dev`

**Use for**: Testing pipeline changes before production deployment

### Monitoring Pipeline (Production)

Regression detection pipeline with degradation checks and Slack alerts.

```bash
oc apply -f pipeline/pipelines/monitoring-pipeline.yaml
```

**Pipeline name**: `abevalflow-monitoring-pipeline`

**Use for**: Scheduled checks and automated triggers (webhooks, cron)

### Monitoring Pipeline (Development)

Development variant of monitoring pipeline.

```bash
oc apply -f pipeline/pipelines/monitoring-pipeline-dev.yaml
```

**Pipeline name**: `abevalflow-monitoring-pipeline-dev`

**Use for**: Testing monitoring changes before production deployment

---

## Verification

### Check All Resources

```bash
# Verify pipelines
oc get pipeline.tekton.dev -n ab-eval-flow

# Verify tasks
oc get task.tekton.dev -n ab-eval-flow

# Verify event listener
oc get eventlistener -n ab-eval-flow

# Verify PVCs
oc get pvc -n ab-eval-flow

# Check event listener pod is running
oc get pods -l eventlistener=submission-listener
```

### Expected Output

**Pipelines (4)**:
- `abevalflow-pipeline` (CI production)
- `abevalflow-pipeline-dev` (CI development)
- `abevalflow-monitoring-pipeline` (monitoring production)
- `abevalflow-monitoring-pipeline-dev` (monitoring development)

**Tasks (19)**: validate, generate-tests, scaffold, build-push, harbor-eval, ase-eval, mcpchecker-eval, security-scan, test-quality-review, analyze-and-check-degradation, store, cleanup-pvc, and more

**EventListener**: `submission-listener` at `http://el-submission-listener.ab-eval-flow.svc.cluster.local:8080`

**PVCs (2)**: `abevalflow-workspace`, `abevalflow-dead-letter`

---

## Quick Deployment (All Pipelines)

Run all commands in sequence to deploy everything:

```bash
# 1. RBAC
oc apply -f config/rbac.yaml

# 2. ConfigMaps
oc apply -f config/pipeline_defaults.yaml
oc apply -f pipeline/configmaps/

# 3. Storage
oc apply -f config/storage/workspace_pvc.yaml
oc apply -f config/storage/dead_letter_pvc.yaml

# 4. Tasks
oc apply -f pipeline/tasks/components/
oc apply -f pipeline/tasks/phases/
find pipeline/tasks/post -name "*.yaml" ! -name "*_deprecated*" -exec oc apply -f {} \;

# 5. Pipelines
oc apply -f pipeline/pipelines/ci-pipeline.yaml
oc apply -f pipeline/pipelines/ci-pipeline-dev.yaml
oc apply -f pipeline/pipelines/monitoring-pipeline.yaml
oc apply -f pipeline/pipelines/monitoring-pipeline-dev.yaml

# 6. Triggers
oc apply -f pipeline/triggers/trigger-template.yaml
oc apply -f pipeline/triggers/trigger-binding.yaml
oc apply -f pipeline/triggers/litellm-config-template.yaml
oc apply -f pipeline/triggers/litellm-config-binding.yaml
oc apply -f pipeline/triggers/quay-push-template.yaml
oc apply -f pipeline/triggers/quay-push-binding.yaml
oc apply -f pipeline/triggers/event-listener.yaml

# Verify
oc get pipeline.tekton.dev,task.tekton.dev,eventlistener -n ab-eval-flow
```

---

## Updating Specific Components

When you've already deployed everything and only need to update specific components after making changes.

### Update a Specific Task

```bash
# Update a single component task
oc apply -f pipeline/tasks/components/harbor-eval.yaml

# Update a single phase task
oc apply -f pipeline/tasks/phases/evaluate.yaml

# Update a single post-processing task
oc apply -f pipeline/tasks/post/store.yaml

# Verify the update
oc get task.tekton.dev/<task-name> -n ab-eval-flow -o yaml | grep 'resourceVersion:'
```

**Example**: After modifying `harbor-eval.yaml`:
```bash
oc apply -f pipeline/tasks/components/harbor-eval.yaml
oc get task.tekton.dev/harbor-eval -n ab-eval-flow
```

### Update a Specific Pipeline

```bash
# Update CI pipeline (production)
oc apply -f pipeline/pipelines/ci-pipeline.yaml

# Update CI pipeline (development)
oc apply -f pipeline/pipelines/ci-pipeline-dev.yaml

# Update monitoring pipeline (production)
oc apply -f pipeline/pipelines/monitoring-pipeline.yaml

# Update monitoring pipeline (development)
oc apply -f pipeline/pipelines/monitoring-pipeline-dev.yaml

# Verify the update
oc get pipeline.tekton.dev/<pipeline-name> -n ab-eval-flow
```

**Example**: After modifying the CI pipeline definition:
```bash
oc apply -f pipeline/pipelines/ci-pipeline.yaml
oc get pipeline.tekton.dev/abevalflow-pipeline -n ab-eval-flow
```

### Update ConfigMaps

```bash
# Update pipeline defaults
oc apply -f config/pipeline_defaults.yaml

# Update a specific monitoring configmap
oc apply -f pipeline/configmaps/monitoring-canary-pack.yaml

# Update all monitoring configmaps
oc apply -f pipeline/configmaps/

# Verify the update
oc get configmap <configmap-name> -n ab-eval-flow -o yaml
```

**Note**: ConfigMap changes do not affect running PipelineRuns, only new ones.

### Update Trigger Resources

```bash
# Update a specific trigger template
oc apply -f pipeline/triggers/trigger-template.yaml

# Update a specific trigger binding
oc apply -f pipeline/triggers/trigger-binding.yaml

# Update the event listener
oc apply -f pipeline/triggers/event-listener.yaml

# Verify the update
oc get triggertemplate,triggerbinding,eventlistener -n ab-eval-flow
```

**Note**: EventListener updates trigger a pod restart automatically.

### Update RBAC

```bash
# Update RBAC permissions
oc apply -f config/rbac.yaml

# Verify the update
oc get serviceaccount,role,rolebinding -n ab-eval-flow | grep pipeline
```

**Note**: RBAC changes take effect immediately for new PipelineRuns.

### Update Multiple Related Components

When you've modified several related files (e.g., multiple tasks):

```bash
# Update all component tasks
oc apply -f pipeline/tasks/components/

# Update all phase tasks
oc apply -f pipeline/tasks/phases/

# Update all post-processing tasks (excluding deprecated)
find pipeline/tasks/post -name "*.yaml" ! -name "*_deprecated*" -exec oc apply -f {} \;

# Update all pipelines
oc apply -f pipeline/pipelines/

# Update all triggers
oc apply -f pipeline/triggers/
```

### Testing Updates

After updating components, test with a new PipelineRun:

```bash
# Quick test with ASE monitoring pipeline
oc create -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: test-update-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-monitoring-pipeline
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/skill-submissions.git"
    - name: revision
      value: "eval/hello-world-full"
    - name: submission-dir
      value: "hello-world-full"
    - name: eval-engine
      value: "ase"
    - name: pipeline-repo-revision
      value: "main"
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
YAML

# Watch for issues
oc get pipelinerun -n ab-eval-flow --watch
```

### Common Update Scenarios

| What Changed | Command | Notes |
|--------------|---------|-------|
| Modified `harbor-eval.yaml` | `oc apply -f pipeline/tasks/components/harbor-eval.yaml` | Only affects new runs |
| Modified CI pipeline params | `oc apply -f pipeline/pipelines/ci-pipeline.yaml` | Existing runs continue with old definition |
| Modified trigger template | `oc apply -f pipeline/triggers/trigger-template.yaml` | Affects next webhook trigger |
| Modified EventListener | `oc apply -f pipeline/triggers/event-listener.yaml` | Pod restarts automatically |
| Modified multiple tasks | `oc apply -f pipeline/tasks/components/` | Batch update |
| Modified RBAC permissions | `oc apply -f config/rbac.yaml` | Takes effect immediately |

### Rollback a Change

If an update causes issues, revert to the previous version:

```bash
# Option 1: Revert your local file and reapply
git checkout HEAD~1 pipeline/tasks/components/harbor-eval.yaml
oc apply -f pipeline/tasks/components/harbor-eval.yaml

# Option 2: Delete and recreate from main branch
oc delete task.tekton.dev/harbor-eval -n ab-eval-flow
git checkout main pipeline/tasks/components/harbor-eval.yaml
oc apply -f pipeline/tasks/components/harbor-eval.yaml
```

---

## Testing Your Deployment

Trigger a simple test run to verify everything works:

```bash
# ASE Monitoring pipeline (simplest test)
oc create -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  generateName: ase-verify-
  namespace: ab-eval-flow
spec:
  pipelineRef:
    name: abevalflow-monitoring-pipeline
  params:
    - name: repo-url
      value: "https://github.com/RHEcosystemAppEng/skill-submissions.git"
    - name: revision
      value: "eval/hello-world-full"
    - name: submission-dir
      value: "hello-world-full"
    - name: eval-engine
      value: "ase"
    - name: pipeline-repo-revision
      value: "main"
  workspaces:
    - name: shared-workspace
      volumeClaimTemplate:
        spec:
          accessModes: ["ReadWriteOnce"]
          resources:
            requests:
              storage: 1Gi
YAML

# Watch the run
oc get pipelinerun -n ab-eval-flow --watch
```

For more test examples, see `Docs/manual_trigger_guide.md` or run:

```bash
./scripts/misc/trigger_test_runs.sh main
```

---

## Notes

- All secrets must use the exact names referenced in `config/rbac.yaml` (line 44)
- The EventListener creates a Route automatically for external webhook access
- For production deployments, ensure MinIO and PostgreSQL are configured and accessible
- Monitoring pipelines require baseline data in PostgreSQL for degradation checks to work
