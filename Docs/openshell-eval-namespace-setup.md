# Running a skill evaluation in your own namespace

End-to-end: clone, deploy, configure, run, read the results. Nothing here is
tied to a particular namespace — substitute your own for `$NS` throughout.

Two repositories are involved. This one carries the pipeline, the eval stack and
the gateway configuration; `agent-eval-harness` carries the OpenShell engine the
evaluate step runs. The pipeline clones the harness at run time (see the
`agent-eval-harness-repo-url` and `-revision` parameters), so a local harness
clone is only needed if you intend to change it.

The two must be paired: the pipeline sets `AGENT_EVAL_MODEL_BASE_URL` and the
harness consumes it, for both the provider endpoint and the sandbox policy.

## Clone

```bash
git clone <agentic_eval_flow>    # this repository
cd agentic_eval_flow
export NS=<your-namespace>
```

## Deploy the eval stack

Postgres, minio, MLflow and LiteLLM. The manifests carry no namespace, so `-n`
decides where they land.

```bash
oc new-project "$NS"      # or: oc project "$NS"

# credentials you cannot generate: copy from a namespace that has them
for s in inference ghcr-rh-forge-auth forge-images-pull; do
  oc get secret "$s" -n <source-namespace> -o json \
    | jq 'del(.metadata.namespace,.metadata.uid,.metadata.resourceVersion,
              .metadata.creationTimestamp,.metadata.ownerReferences,
              .metadata.managedFields,.metadata.annotations)' \
    | oc apply -n "$NS" -f -
done

# credentials you should generate fresh
PGPASS=$(openssl rand -hex 16); MINIOPASS=$(openssl rand -hex 20)
oc create secret generic ab-eval-db-credentials -n "$NS" \
  --from-literal=postgres-password="$PGPASS" \
  --from-literal=database-url="postgresql+psycopg://abevalflow:${PGPASS}@postgres:5432/abevalflow"
oc create secret generic minio-credentials -n "$NS" \
  --from-literal=root-user=abevalflow --from-literal=root-password="$MINIOPASS" \
  --from-literal=endpoint-url="http://minio:9000"
oc label secret ab-eval-db-credentials minio-credentials -n "$NS" \
  app.kubernetes.io/part-of=abevalflow --overwrite

oc apply -n "$NS" -f config/postgres/ -f config/storage/minio.yaml \
                  -f config/mlflow/ -f config/litellm/
oc get pods -n "$NS" -w        # wait for 4/4 Running
```

## Deploy the OpenShell gateway VM

The agent runs in a sandbox on this VM. Needs OpenShift Virtualization.

```bash
ssh-keygen -t ed25519 -N "" -f ~/.forge-eval-keys/sandbox-ssh
oc create secret generic openshell-aap-ssh -n "$NS" \
  --from-file=key=$HOME/.forge-eval-keys/sandbox-ssh \
  --from-file=public_key=$HOME/.forge-eval-keys/sandbox-ssh.pub

helm upgrade --install saw <forge-saw>/charts/openshell-saw -n "$NS" \
  -f config/forge-saw/values-namespace-gateway.yaml \
  --set sandboxName=saw \
  --set sshPublicKey="$(cat ~/.forge-eval-keys/sandbox-ssh.pub)" \
  --set oidc.issuerUrl=https://<keycloak-host>/realms/<realm>

# the setup Job pulls a private image, so its ServiceAccount needs the pull secret
oc secrets link saw-setup ghcr-rh-forge-auth --for=pull -n "$NS"
```

**forge-saw version.** `source.pullMethod: pod` in the values only takes effect
on a chart that honours it. Older checkouts hardcode `pullMethod: node`, which
cannot use `registryAuthSecret`, and the disk import then fails with
`unable to retrieve auth token: invalid username/password`. Either use a chart
containing the `source.pullMethod` fix, or patch the VM once after install:

```bash
oc patch vm saw -n "$NS" --type=json -p='[
  {"op":"replace","path":"/spec/dataVolumeTemplates/0/spec/source/registry/pullMethod","value":"pod"},
  {"op":"replace","path":"/spec/dataVolumeTemplates/0/spec/source/registry/secretRef","value":"forge-images-pull"}]'
oc delete dv saw-root -n "$NS"     # re-imports with the corrected spec
```

Checkouts older than forge-saw `f6d5934` also fail the setup Job with
`setup image is missing required tool: openssh`; that check was corrected
upstream to look for `ssh`, `scp` and `ssh-keygen`.

Disk import plus VM boot takes 15–25 minutes. It is ready when
`oc get job saw-setup -n "$NS"` reports Complete.

## Configure the gateway and the policies

```bash
oc apply -n "$NS" -f config/forge-saw/networkpolicy-eval-stack.yaml \
                  -f config/forge-saw/networkpolicy-saw-gateway.yaml

NS="$NS" SANDBOX=saw MODEL=<model-id> LITELLM_URL=http://litellm:4000/v1 \
  ./config/forge-saw/gateway-postinstall.sh
```

That script does the three things a fresh gateway needs: exports its mTLS client
identity into `openshell-mtls`, adds the CI OIDC subject to the gateway
workspace, and sets the provider and inference route. Pass `SUBJECT=<oidc-sub>`
if you know it; otherwise run `openshell whoami` from a first run's log and
re-run the script.

Check the plumbing before spending 20 minutes on a run:

```bash
oc apply -n "$NS" -f pipeline/tasks/eval-stack-probe.yaml
oc create -n "$NS" -f - <<'YAML'
apiVersion: tekton.dev/v1
kind: TaskRun
metadata: {generateName: probe-}
spec:
  taskRef: {name: eval-stack-probe}
  serviceAccountName: pipeline
YAML
oc logs -n "$NS" -l tekton.dev/task=eval-stack-probe -c step-probe --tail=20
```

All six checks should say PASS. Anything failing is a NetworkPolicy gap, and
`Docs/openshell-eval-troubleshooting.md` maps each symptom to its cause.

## Add your skill

A skill under test is a **submission** the pipeline clones:

```
submissions/<name>/
  eval.yaml                      # model, runner, judges, files staged into the sandbox
  metadata.yaml
  scenes/<scene>.yaml            # the fixture the agent sees
  cases/<case>/input.yaml        # the prompt
  cases/<case>/annotations.yaml  # ground truth the judges score against
  workspace/skills/<skill>/SKILL.md
```

`input.yaml` is the task, `annotations.yaml` is the expected answer, and
`judges:` in `eval.yaml` compares them. Your skill reaches the sandbox through
`dataset.workspace.files`, where `dest` is the path under `/sandbox`. Judges
have `if:` conditions keyed on annotation fields, so a case that omits a field
simply skips those judges.

Copy `submissions/openclaw-forge` as a starting point, then push to your fork:

```bash
cp -r submissions/openclaw-forge submissions/my-skill
# edit eval.yaml, cases/, workspace/skills/
git checkout -b eval/my-skill && git add submissions/my-skill
git commit -m "add my-skill submission" && git push origin eval/my-skill
```

Write cases that only need data your namespace actually has. The stock
`openclaw-forge` cases expect a seeded Microsoft 365 mailbox; without those
credentials the agent scores low for reasons unrelated to your skill.

## Run the evaluation

```bash
oc apply  -n "$NS" -f pipeline/tasks/ -f pipeline/pipelines/ci-pipeline-openshell.yaml
oc create -n "$NS" -f pipeline/runs/openshell-openclaw-pipelinerun.yaml
```

Point the run at your submission by editing the params in that file, or inline:

| Param | Set it to |
|---|---|
| `repo-url`, `pipeline-repo-url` | your `agentic_eval_flow` fork |
| `revision`, `pipeline-repo-revision` | your branch, e.g. `eval/my-skill` |
| `submission-dir` | `my-skill` |
| `openshell-gateway-endpoint` | `https://saw-gateway:17670` (your Helm release) |
| `llm-api-base` | `http://litellm:4000` |
| `aeh-judge-model-override` | `judge-glm-5-3` — see 7.7 |

A run takes roughly 10 minutes per case.

## Read the results

**Console** — Pipelines → your run, for the graph and per-step logs.

**CLI**

```bash
oc get pipelinerun -n "$NS"
oc logs -n "$NS" <run>-evaluate-pod -c step-aeh-openshell-eval -f
oc get pipelinerun <run> -n "$NS" -o jsonpath='{.status.results}' | jq .
```

The results carry the verdict: `recommendation`, `mean-reward-gap`,
`scorecard-recommendation`.

**MLflow** — one experiment per PipelineRun, with per-judge metrics:

```bash
oc port-forward -n "$NS" svc/abevalflow-mlflow 5000:5000   # then http://localhost:5000
```

Keep it on port-forward rather than a Route: MLflow has no authentication.

What the numbers mean: `pass_rate` is how many cases the deterministic judges
passed, `mean_reward` is the LLM judges' average, and `recommendation` is
`mean_reward` against the threshold. A judge reported as `ERR` did not run —
that is a plumbing problem, not a low score. If all the LLM judges error with
403, the judge model is going through the upstream `/responses` API; use
`aeh-judge-model-override=judge-glm-5-3`, the LiteLLM entry that routes the same
upstream as `hosted_vllm`.

**Known gap:** artifact upload to MLflow fails with `NoCredentialsError`, so
metrics land but `report.html` does not. The evaluate step needs
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `MLFLOW_S3_ENDPOINT_URL` from
`minio-credentials`. Until then, read scores from the logs or MLflow.
