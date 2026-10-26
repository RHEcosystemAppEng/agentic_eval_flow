# Using the MCP Eval Bundle

This guide shows how to consume the bundled MCP evaluation pipeline from Quay.io without copying YAML files.

## What's in the Bundle

The bundle contains:
- `mcp-static-eval` Task — offline source code scanning (secrets, user code, licenses)
- `mcp-conformance-eval` Task — protocol/schema conformance checks against running server
- `mcp-behavioral-eval` Task — behavioral testing via mcpchecker
- `mcp-compass-publish` Task — publish results as facts to Red Hat Compass
- `mcp-eval-pipeline` Pipeline — orchestrates all 4 tasks with proper sequencing

**Quay image**: `quay.io/ecosystem-appeng/abevalflow-mcp-eval-bundle:1.0.0@sha256:...`

## Prerequisites

Your cluster must have:
1. **Tekton installed** with bundle resolver enabled (cluster-wide or namespace-scoped)
2. **Pre-deployed MCP server** that the pipeline will evaluate
3. **Kubernetes Service** exposing the MCP server on a known port (e.g., `mcp-server:8080`)
4. **Secret** for Compass API credentials (optional, only for publishing)

## Basic Usage: Resolve Pipeline from Bundle

Create a `PipelineRun` that references the bundled Pipeline from Quay:

```yaml
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  name: eval-my-mcp-server
  namespace: default
spec:
  pipelineRef:
    resolver: bundles
    params:
      - name: bundle
        value: quay.io/ecosystem-appeng/abevalflow-mcp-eval-bundle:1.0.0@sha256:abc123def456...
      - name: name
        value: mcp-eval-pipeline
      - name: kind
        value: pipeline
  workspaces:
    - name: workspace
      volumeClaimTemplate:
        spec:
          accessModes:
            - ReadWriteOnce
          resources:
            requests:
              storage: 5Gi
  params:
    - name: submission-name
      value: my-mcp-server
    - name: namespace-name
      value: default
    - name: service-name
      value: mcp-server
    - name: service-port
      value: "8080"
    - name: entity-ref
      value: "mcpserver:default/my-mcp-server"
    - name: mcp-eval-image
      value: "quay.io/ecosystem-appeng/abevalflow-mcp-eval:0.1"
    - name: source-subdir
      value: "."
    - name: static-eval-mode
      value: "report"
    - name: compass-url
      value: "https://compass.stage.redhat.com/api/soundcheck/facts/"
    - name: compass-dry-run
      value: "true"
```

Replace:
- `sha256:abc123def456...` with the actual digest (find it via `make digests` in agentic_eval_flow repo)
- `submission-name` with your MCP server name
- `service-name` with your K8s Service name
- `entity-ref` with the Compass entity reference for your server

## Secret Setup: Compass API Key (Optional)

If publishing to Compass (set `compass-dry-run: false`), create the secret:

```bash
kubectl create secret generic compass-api-credentials \
  --from-literal=COMPASS_API_KEY='your-api-key' \
  -n default
```

The bundled `mcp-compass-publish` Task looks for this secret automatically.

## Dry-Run Mode (Recommended First)

Keep `compass-dry-run: true` on first run. The pipeline will:
1. Run all three evaluation phases
2. Log Compass facts to task logs (no actual publish)
3. Write phase reports to the workspace

Check the logs:
```bash
kubectl logs -f pipelinerun/eval-my-mcp-server
```

## Live Publishing to Compass

Once dry-run succeeds, enable live publishing:

```yaml
    - name: compass-dry-run
      value: "false"
```

The `mcp-compass-publish` Task will:
1. Read phase reports from workspace
2. Publish facts to Compass API
3. Report success/failure status

## Example: Full Consumer PipelineRun with Script

Save this as `evaluate-mcp.yaml` and customize:

```yaml
apiVersion: tekton.dev/v1
kind: PipelineRun
metadata:
  name: eval-demo-mcp
spec:
  pipelineRef:
    resolver: bundles
    params:
      - name: bundle
        value: quay.io/ecosystem-appeng/abevalflow-mcp-eval-bundle:1.0.0@sha256:abc123def456...
      - name: name
        value: mcp-eval-pipeline
      - name: kind
        value: pipeline
  workspaces:
    - name: workspace
      volumeClaimTemplate:
        spec:
          accessModes:
            - ReadWriteOnce
          resources:
            requests:
              storage: 5Gi
  params:
    - name: submission-name
      value: demo-mcp
    - name: namespace-name
      value: default
    - name: service-name
      value: mcp-server
    - name: service-port
      value: "8080"
    - name: entity-ref
      value: "mcpserver:default/demo-mcp"
    - name: mcp-eval-image
      value: "quay.io/ecosystem-appeng/abevalflow-mcp-eval:0.1"
    - name: source-subdir
      value: "."
    - name: static-eval-mode
      value: "report"
    - name: compass-url
      value: "https://compass.stage.redhat.com/api/soundcheck/facts/"
    - name: compass-dry-run
      value: "true"
```

Apply:
```bash
kubectl apply -f evaluate-mcp.yaml
kubectl wait --for=condition=Succeeded pipelinerun/eval-demo-mcp --timeout=10m
kubectl logs -f pipelinerun/eval-demo-mcp
```

## Output: Where Are the Reports?

After the pipeline succeeds, reports are in the PVC:
- `reports/demo-mcp/phase-1-static-eval.json` — static findings
- `reports/demo-mcp/phase-2-conformance-eval.json` — conformance results
- `reports/demo-mcp/phase-3-behavioral-eval.json` — mcpchecker results

Extract them:
```bash
kubectl exec -it <pod> -- cat /workspace/reports/demo-mcp/phase-1-static-eval.json
```

Or configure a persistent PVC and copy post-run:
```bash
kubectl cp default/<pod>:/workspace/reports ./reports
```

## Troubleshooting

### Bundle Not Found
```
failed to resolve bundle: ...
```
- Verify bundle resolver is enabled: `kubectl get configmap tekton-config-artifact-bucket -n tekton-pipelines`
- Check Quay image exists: `podman pull quay.io/ecosystem-appeng/abevalflow-mcp-eval-bundle:1.0.0`

### Task Not in Bundle
```
unable to find Task "mcp-static-eval" in bundle
```
- Verify bundle contains the task: `tkn bundle list quay.io/ecosystem-appeng/abevalflow-mcp-eval-bundle:1.0.0`

### Server Unreachable
```
Connection refused: mcp-server:8080
```
- Verify MCP server is running in the target namespace
- Check Service DNS: `kubectl exec -it <pod> -- nslookup mcp-server.default.svc.cluster.local`
- Ensure network policies allow internal communication

### Compass API Key Missing
```
COMPASS_API_KEY not set
```
- Create secret (see Secret Setup above)
- Verify secret exists: `kubectl get secret compass-api-credentials`

## Next Steps

Once you have the pipeline running:
1. Review phase reports in workspace
2. Set `compass-dry-run: false` for live publishing
3. Check Compass UI to see published facts as part of your MCP track
4. Integrate this into your CI/CD (GitOps, ArgoCD, etc.)

