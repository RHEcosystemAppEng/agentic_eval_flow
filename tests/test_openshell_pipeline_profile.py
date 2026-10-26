"""Tests for the OpenShell OpenClaw pipeline profile."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "pipeline" / "pipelines" / "ci-pipeline-openshell.yaml"
HARNESS_PIN = "8d58e500d0eb55a3106819ccacf26da6bf7ea166"
CI = REPO / "pipeline" / "pipelines" / "ci-pipeline.yaml"
CI_DEV = REPO / "pipeline" / "pipelines" / "ci-pipeline-dev.yaml"
SUBMISSION = REPO / "submissions" / "openclaw-forge"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _param_defaults(spec: dict) -> dict[str, str | None]:
    return {p["name"]: p.get("default") for p in spec["params"]}


def _task_names(spec: dict) -> list[str]:
    return [t["name"] for t in spec["tasks"]]


class TestOpenshellPipelineProfile:
    def test_submission_uses_only_image_owned_chief_of_staff_assets(self):
        config = _load(SUBMISSION / "eval.yaml")
        assert config["dataset"]["workspace"]["files"] == []
        prompt = config["runner"]["system_prompt"]
        assert "immutable Forge SAW image owns" in prompt
        assert not (SUBMISSION / "CLAW.md").exists()
        assert not any(p.is_file() for p in (SUBMISSION / "workspace").rglob("*"))
        assert not any(p.is_file() for p in (SUBMISSION / "adapters").rglob("*"))

    def test_named_pipeline_omits_test_and_red_team(self):
        spec = _load(PIPELINE)["spec"]
        names = _task_names(spec)
        assert names == ["prepare", "evaluate", "analyze", "store"]
        assert "test" not in names
        assert "red-team" not in names
        evaluate = next(t for t in spec["tasks"] if t["name"] == "evaluate")
        assert evaluate["runAfter"] == ["prepare"]

    def test_openshell_defaults(self):
        defaults = _param_defaults(_load(PIPELINE)["spec"])
        assert defaults["eval-engine"] == "aeh_openshell_openclaw"
        assert defaults["submission-dir"] == "openclaw-forge"
        assert defaults["enable-ai-generation"] == "false"
        assert defaults["aeh-runner"] == "openshell"
        assert defaults["aeh-openshell-image"] == "registry.access.redhat.com/ubi9/python-311:9.6"
        assert defaults["agent-eval-harness-repo-revision"] == HARNESS_PIN
        assert defaults["openshell-user-fixture"] == "fixtures/USER.md"
        assert defaults["openshell-sandbox-image"] == (
            "ghcr.io/rh-forge/openclaw-saw-agent@sha256:b47b92a6b3fd03327c1f2093a5c28aba0fdf3cb620e9154335688900191fe2b9"
        )
        assert defaults["llm-model"] == "rits/zai-org/GLM-5-3-Flash"
        assert defaults["llm-generation-model"] == "rits/zai-org/GLM-5-3-Flash"
        assert defaults["aeh-model-override"] == "inference/rits/zai-org/GLM-5-3-Flash"
        # A plain openai/ judge model 403s on /v1/messages; the judge-glm-5-3
        # hosted_vllm route in config/litellm/configmap.yaml stays on
        # /chat/completions. See that file for the full rationale.
        assert defaults["aeh-judge-model-override"] == "judge-glm-5-3"
        # Alias Service from config/forge-saw/openshell-eval-alias.yaml; keeps
        # the default namespace-agnostic.
        assert defaults["openshell-gateway-endpoint"] == "https://openshell:17670"
        assert defaults["enable-mlflow"] == "true"
        analyze = next(t for t in _load(PIPELINE)["spec"]["tasks"] if t["name"] == "analyze")
        scan = next(p for p in analyze["params"] if p["name"] == "security-scan-mode")
        assert scan["value"] == "disabled"

    def test_litellm_configmap_has_flash_and_judge_routes(self):
        config = _load(REPO / "config" / "litellm" / "configmap.yaml")
        models = yaml.safe_load(config["data"]["config.yaml"])["model_list"]
        names = {entry["model_name"] for entry in models}
        assert "rits/zai-org/GLM-5-3-Flash" in names
        assert "judge-glm-5-3" in names
        judge = next(e for e in models if e["model_name"] == "judge-glm-5-3")
        assert judge["litellm_params"]["model"].startswith("hosted_vllm/")

    def test_gateway_alias_service_selects_agent_vmi(self):
        alias = _load(REPO / "config" / "forge-saw" / "openshell-eval-alias.yaml")
        assert alias["spec"]["selector"] == {
            "app.kubernetes.io/name": "openshell-saw-agent",
            "vm.kubevirt.io/name": "openshell-saw-agent",
        }
        ports = alias["spec"]["ports"]
        assert ports == [{"name": "gateway", "port": 17670, "protocol": "TCP", "targetPort": 17670}]

    def test_pipeline_defaults_are_namespace_agnostic(self):
        defaults = _param_defaults(_load(PIPELINE)["spec"])
        for name in ("llm-api-base", "llm-base-url", "mlflow-tracking-uri", "openshell-gateway-endpoint"):
            value = defaults[name]
            assert ".svc" not in value, f"{name} pins a namespace: {value}"
        assert defaults["llm-api-base"] == "http://litellm:4000"
        assert defaults["llm-base-url"] == "http://litellm:4000/v1"
        assert defaults["mlflow-tracking-uri"] == "http://abevalflow-mlflow:5000"
        # Single source of truth: the example run must not duplicate the
        # endpoint defaults the Pipeline already provides.
        run = _load(REPO / "pipeline" / "runs" / "openshell-openclaw-pipelinerun.yaml")
        example_params = {p["name"] for p in run["spec"]["params"]}
        for name in ("llm-api-base", "llm-base-url", "mlflow-tracking-uri", "openshell-gateway-endpoint"):
            assert name not in example_params, f"example run duplicates Pipeline default {name}"

    def test_example_run_is_namespace_agnostic(self):
        run = _load(REPO / "pipeline" / "runs" / "openshell-openclaw-pipelinerun.yaml")
        params = {p["name"]: p["value"] for p in run["spec"]["params"]}
        # Endpoint defaults live in the Pipeline and are namespace-local
        # short names; the example must not reintroduce namespace pins.
        for name, value in params.items():
            assert ".svc.cluster.local" not in value, f"{name} pins a namespace: {value}"
        pod_template = run["spec"]["taskRunTemplate"].get("podTemplate", {})
        assert "hostAliases" not in pod_template
        timeouts = run["spec"]["timeouts"]
        assert timeouts["pipeline"] == "3h0m0s"
        assert timeouts["tasks"] == "2h30m0s"
        # Must stay above the cleanup finally task's internal budgets
        # (120s pod diagnostics + 300s PVC confirmation + API-retry
        # headroom, ~10m worst case) so a stuck holder ends in the task's
        # own conservative "failed" report, not a Tekton timeout.
        assert timeouts["finally"] == "15m0s"

    def test_cleanup_task_is_best_effort(self):
        task = _load(REPO / "pipeline" / "tasks" / "post" / "cleanup_pvc.yaml")
        step = task["spec"]["steps"][0]
        assert step.get("onError") == "continue"
        assert "--request-timeout=20s" in step["script"]
        # The openshift/cli samples imagestream is pullable by any SA; a
        # private repo image only works where it is already node-cached.
        assert step["image"] == "image-registry.openshift-image-registry.svc:5000/openshift/cli:latest"

    def test_cleanup_failures_are_operator_visible(self):
        task = _load(REPO / "pipeline" / "tasks" / "post" / "cleanup_pvc.yaml")
        assert "cleanup-status" in {r["name"] for r in task["spec"]["results"]}
        script = task["spec"]["steps"][0]["script"]
        assert "$(results.cleanup-status.path)" in script
        for outcome in ("api-unavailable", "none", "failed", "deleted"):
            assert f'echo "{outcome}" > "$RESULT_PATH"' in script
        # A failed delete labels the leaked PVC instead of failing the run.
        assert "abevalflow.io/cleanup-failed=true" in script
        pipeline = _load(PIPELINE)
        result = next(r for r in pipeline["spec"]["results"] if r["name"] == "cleanup-status")
        assert result["value"] == "$(finally.cleanup.results.cleanup-status)"
        # Labelling the PVC needs patch on persistentvolumeclaims.
        rbac = list(yaml.safe_load_all((REPO / "config" / "rbac.yaml").read_text()))
        role = next(o for o in rbac if o and o["kind"] == "Role" and o["metadata"]["name"] == "pipeline-trial-manager")
        pvc_rule = next(r for r in role["rules"] if "persistentvolumeclaims" in r["resources"])
        assert "patch" in pvc_rule["verbs"]

    def test_cleanup_releases_retained_taskrun_pods(self):
        # Tekton retains a run's completed TaskRun pods, and while those
        # pod objects reference the workspace PVC the pvc-protection
        # finalizer never clears: the delete is accepted but the PVC
        # stays Terminating forever (observed: 58 of 70 PVCs in the
        # verification namespace stuck, some for 10+ days). The cleanup
        # task must release those pods before deleting the PVCs.
        task = _load(REPO / "pipeline" / "tasks" / "post" / "cleanup_pvc.yaml")
        assert _param_defaults(task["spec"])["delete-retained-pods"] == "true"
        script = task["spec"]["steps"][0]["script"]
        # memberOf=tasks matches only DAG pods, never the finally pod
        # running this script, so the cleanup cannot delete itself.
        selector = "tekton.dev/pipelineRun=$(params.pipelinerun-name),tekton.dev/memberOf=tasks"
        assert selector in script
        assert "could not delete retained TaskRun pods" in script
        # The run's Affinity Assistant StatefulSet is a second holder: for
        # volumeClaimTemplate workspaces its nop pod mounts the workspace
        # PVC, and Tekton deletes it only at PipelineRun completion —
        # after the finally cleanup task ends. Without releasing it here
        # no confirmation window can ever see the reclaim (runs 7-9
        # "failed" with multi-minute windows for exactly this reason);
        # with it released, measured reclaim is ~2-5s. The task must
        # delete the run-scoped assistant, and the window is a margin
        # over that measured reclaim, not a guess at latency.
        assert 'oc delete statefulset -n "${NAMESPACE}"' in script
        assert '-l "tekton.dev/pipelineRun=$(params.pipelinerun-name)"' in script
        assert "could not delete the run's affinity assistant" in script
        # "deleted" must mean confirmed removed, not delete-accepted: the
        # script polls for actual disappearance and labels what lingers.
        # The pod release itself is two steps — the delete, then a bounded
        # wait for the pod objects to be gone (a pod with a
        # deletionTimestamp still mounts the PVC and holds the finalizer).
        # The pod wait is a diagnostic bound, not the completion gate (the
        # PVC loop is), so both budgets together with API-retry headroom
        # (~10m worst case) must stay inside the example run's 15m finally
        # timeout — pinned here and in the example-run test so neither can
        # silently outgrow it.
        assert "Retained TaskRun pods confirmed gone" in script
        assert "Waiting for PVC deletion to complete" in script
        assert "SECONDS + 120" in script
        assert "SECONDS + 300" in script
        # A get that fails for any reason other than NotFound must not be
        # read as "PVC gone".
        assert "(NotFound)" in script
        # Deleting the run's pods and affinity assistant needs delete on
        # both (pods is already granted for the trial-pod lifecycle; pin
        # both so neither can be dropped).
        rbac = list(yaml.safe_load_all((REPO / "config" / "rbac.yaml").read_text()))
        role = next(o for o in rbac if o and o["kind"] == "Role" and o["metadata"]["name"] == "pipeline-trial-manager")
        pod_rule = next(r for r in role["rules"] if r["resources"] == ["pods"])
        assert "delete" in pod_rule["verbs"]
        sts_rule = next(r for r in role["rules"] if r["resources"] == ["statefulsets"])
        assert sts_rule["apiGroups"] == ["apps"]
        assert "delete" in sts_rule["verbs"]

    def test_cleanup_is_reclaim_only(self):
        # The cleanup task's contract is reclaiming storage, not
        # reporting: it makes no model calls and formats nothing — run
        # reporting lives in MLflow/MinIO and in
        # scripts/pipelinerun-report.sh, which reads the TaskRun objects
        # that survive the release, so no model credentials sit on the
        # reclamation path. (It does read the pods' logs — once, as a
        # byte-for-byte archive of what its own release destroys.)
        task = _load(REPO / "pipeline" / "tasks" / "post" / "cleanup_pvc.yaml")
        script = task["spec"]["steps"][0]["script"]
        for absent in (
            "digest",
            "oc get taskrun",
            "python3 -c",
            "/v1/chat/completions",
        ):
            assert absent not in script
        params = {p["name"] for p in task["spec"]["params"]}
        for name in ("llm-model", "llm-api-base", "llm-api-key"):
            assert name not in params
        # The pipeline forwards no LLM params to the finally task, and
        # surfaces the pod-release switch as a Pipeline-level parameter.
        cleanup = next(t for t in _load(PIPELINE)["spec"]["finally"] if t["name"] == "cleanup")
        forwarded = {p["name"]: p["value"] for p in cleanup["params"]}
        for name in ("llm-model", "llm-api-base", "llm-api-key"):
            assert name not in forwarded
        assert forwarded["delete-retained-pods"] == "$(params.delete-retained-pods)"
        pipe_params = {p["name"]: p for p in _load(PIPELINE)["spec"]["params"]}
        assert pipe_params["delete-retained-pods"]["default"] == "true"
        # With no consumer left, the pipeline ServiceAccount keeps no
        # tekton.dev grant.
        rbac = list(yaml.safe_load_all((REPO / "config" / "rbac.yaml").read_text()))
        role = next(o for o in rbac if o and o["kind"] == "Role" and o["metadata"]["name"] == "pipeline-trial-manager")
        assert all(r.get("apiGroups") != ["tekton.dev"] for r in role["rules"])

    def test_cleanup_archives_pod_logs_before_release(self):
        # The pod release destroys the run's complete per-step logs, so
        # they are archived to MinIO first — fail-safe: if the archive
        # does not complete, the pods are kept and the PVC is reported
        # failed; logs are never destroyed unarchived.
        task = _load(REPO / "pipeline" / "tasks" / "post" / "cleanup_pvc.yaml")
        step = task["spec"]["steps"][0]
        script = step["script"]
        # One file per TaskRun pod (retries are distinct TaskRuns),
        # every step of the task in it.
        assert "--all-containers" in script
        assert "metadata.labels.tekton\\.dev/taskRun" in script
        # The archive must precede the release, or there is nothing
        # left to read.
        assert script.index("Archiving the run's step pod logs") < script.index("oc delete pod")
        # The upload runs inside the MinIO deployment's pod, which
        # already has mc and the credentials — no extra image on the
        # cleanup path, and no credentials in this task.
        assert "oc exec" in script and "mc cp" in script
        assert len(task["spec"]["steps"]) == 1
        assert step["image"].endswith("openshift/cli:latest")
        env = {e["name"]: e for e in step["env"]}
        assert env["MINIO_ENDPOINT"]["valueFrom"]["secretKeyRef"]["key"] == "endpoint-url"
        assert env["MINIO_ENDPOINT"]["valueFrom"]["secretKeyRef"]["optional"] is True
        assert "root-user" not in script and "root-password" not in script
        # Fail-safe gating: any archive failure keeps the pods. A
        # missing endpoint is as fatal as an unreachable MinIO — it
        # must not silently release unarchived.
        assert "release_ok=0" in script
        assert "Skipping the pod release" in script
        endpoint_gate = script.split('if [ -z "${MINIO_ENDPOINT:-}" ]; then', 1)[1]
        assert endpoint_gate.index("release_ok=0") < endpoint_gate.index("elif")
        assert "releasing the pods without archiving" not in script
        # Completeness is measured against the pod count from the same
        # listing: a partial fetch keeps the pods exactly like a total
        # one does, and a failed listing is zero knowledge, not zero
        # pods.
        assert "if ! pod_listing=" in script
        assert "could not list the run's pods" in script
        assert "expected=$((expected + 1))" in script
        assert '[ "$archived" -ne "$expected" ]' in script
        assert 'elif [ "$expected" -eq 0 ]' in script
        # The local pull side exists and reads the same layout.
        fetch = (REPO / "scripts" / "fetch_run_logs.sh").read_text()
        assert "debug/pod-logs" in fetch
        assert "deploy/minio" in fetch
        # No pinned namespace default: the current context's, or an
        # explicit one.
        assert ":-forge-nommen" not in fetch
        assert "oc config view --minify" in fetch

    def test_rbac_applies_into_any_namespace(self):
        # metadata.namespace would make `oc apply -n X` fail, and a subject
        # namespace would bind another namespace's ServiceAccount; subjects
        # without a namespace default to the RoleBinding's own namespace.
        rbac = list(yaml.safe_load_all((REPO / "config" / "rbac.yaml").read_text()))
        for obj in rbac:
            assert obj is not None
            assert "namespace" not in obj["metadata"], obj["metadata"]["name"]
            if obj["kind"] == "RoleBinding":
                for subject in obj["subjects"]:
                    assert "namespace" not in subject

    def test_explicit_auth_mode_selects_gateway_path(self):
        # The default alias endpoint must select the mTLS path through an
        # explicit parameter, never through endpoint hostname shape.
        pipeline_defaults = _param_defaults(_load(PIPELINE)["spec"])
        assert pipeline_defaults["openshell-auth-mode"] == "mtls"
        assert pipeline_defaults["openshell-gateway-endpoint"] == "https://openshell:17670"
        evaluate = next(t for t in _load(PIPELINE)["spec"]["tasks"] if t["name"] == "evaluate")
        forwarded = {p["name"]: p["value"] for p in evaluate["params"]}
        assert forwarded["openshell-auth-mode"] == "$(params.openshell-auth-mode)"

        task = _load(REPO / "pipeline" / "tasks" / "phases" / "evaluate.yaml")
        assert _param_defaults(task["spec"])["openshell-auth-mode"] == "mtls"
        step = next(s for s in task["spec"]["steps"] if s["name"] == "aeh-openshell-eval")
        env = next(e for e in step["env"] if e["name"] == "OPENSHELL_AUTH_MODE")
        assert env["value"] == "$(params.openshell-auth-mode)"
        script = step["script"]
        case_head = 'case "${OPENSHELL_AUTH_MODE:-mtls}" in'
        assert case_head in script
        mtls_branch = script.split(case_head, 1)[1].split("oidc)", 1)[0]
        assert "unset OPENSHELL_GATEWAY_INSECURE" in mtls_branch
        assert "--local" in mtls_branch
        oidc_branch = script.split("oidc)", 1)[1].split("*)", 1)[0]
        assert "openshell-auth-mode=oidc requires" in oidc_branch
        assert "OPENSHELL_OIDC_ISSUER" in oidc_branch
        # The endpoint-shape sniffing that used to pick the path is gone.
        assert '*".svc.cluster.local:"*' not in script
        assert '"https://host.containers.internal:"*' not in script

    def test_mtls_staging_is_scoped_to_mtls_branch(self):
        # The mTLS client certificate is an mTLS-only requirement: staging it
        # and its required-file check must live inside the mtls branch, so an
        # OIDC-only deployment (no openshell-mtls Secret) reaches the oidc
        # branch without failing on it.
        task = _load(REPO / "pipeline" / "tasks" / "phases" / "evaluate.yaml")
        step = next(s for s in task["spec"]["steps"] if s["name"] == "aeh-openshell-eval")
        script = step["script"]
        case_head = 'case "${OPENSHELL_AUTH_MODE:-mtls}" in'
        before_case = script.split(case_head, 1)[0]
        mtls_branch = script.split(case_head, 1)[1].split("oidc)", 1)[0]
        oidc_branch = script.split("oidc)", 1)[1].split("*)", 1)[0]
        for token in ("MTLS_SRC", "MTLS_DST", "openshell-mtls client certificate is missing"):
            assert token not in before_case, token
            assert token in mtls_branch, token
            assert token not in oidc_branch, token
        # The oidc branch depends only on the OIDC Secret's own fields.
        for token in ("OPENSHELL_OIDC_ISSUER", "OPENSHELL_OIDC_CLIENT_ID", "OPENSHELL_OIDC_CLIENT_SECRET"):
            assert token in oidc_branch
        # Post-case diagnostics run under set -u with MTLS_DST unset in oidc
        # mode, so they must guard the variable instead of referencing it.
        assert '[ -n "${MTLS_DST:-}" ]' in script

    def test_probe_targets_pipeline_gateway_contract(self):
        probe = _load(REPO / "pipeline" / "tasks" / "eval-stack-probe.yaml")
        pipeline_defaults = _param_defaults(_load(PIPELINE)["spec"])
        probe_default = _param_defaults(probe["spec"])["openshell-gateway-endpoint"]
        # The probe must test the same endpoint the Pipeline uses.
        assert probe_default == pipeline_defaults["openshell-gateway-endpoint"]
        step = probe["spec"]["steps"][0]
        script = step["script"]
        assert 'gateway_probe "openshell agent" "$(params.openshell-gateway-endpoint)"' in script
        # The VMI's raw service name is not the Pipeline's contract.
        assert "openshell-saw-agent-gateway" not in script
        # Three layers: TCP reachability, TLS handshake/validation, HTTP.
        assert "/dev/tcp/" in script
        assert "openssl s_client" in script
        assert "subjectAltName" in script
        assert "curl -sS" in script
        # A failed TLS handshake or certificate validation must fail the TLS
        # layer: openssl s_client is piped into openssl x509, so pipefail must
        # propagate the client's failure status — without it, x509 parsing
        # the still-emitted certificate would pass the check.
        assert "set -uo pipefail" in script
        # TLS chain validation is enabled only when the CA bundle exists, and
        # it must be the CA that signed the gateway's serving certificate:
        # openshell-ca ships in the openshell-mtls bundle, while the forge
        # service CA can never validate the gateway chain (a mismatch the
        # pre-pipefail false-pass used to mask).
        ca = next(e for e in step["env"] if e["name"] == "GATEWAY_CA_CERT")
        ref = ca["valueFrom"]["secretKeyRef"]
        assert ref["name"] == "openshell-mtls"
        assert ref["key"] == "ca.crt"
        assert ref["optional"] is True
        # An HTTP answer is not service health: 5xx responses fail the probe.
        assert "(server error)" in script

    def test_evaluate_openshell_step_logs_mlflow(self):
        evaluate = (REPO / "pipeline" / "tasks" / "phases" / "evaluate.yaml").read_text()
        openshell = evaluate.split("name: aeh-openshell-eval", 1)[1].split("name: aeh-pairwise", 1)[0]
        assert "log_aeh_mlflow.py" in openshell
        assert "=== AEH OpenShell MLflow logging ===" in openshell
        assert "Skipping AEH OpenShell MLflow" in openshell
        # AEH harness sees the tracking URI during the run; CI logger runs after.
        uri_note = "MLFLOW_TRACKING_URI set for AEH harness + CI logger"
        assert uri_note in openshell
        assert openshell.find(uri_note) < openshell.find("scripts/run_aeh.py")

    def test_openshell_eval_uses_llm_param_instead_of_inference_secret(self):
        task = _load(REPO / "pipeline" / "tasks" / "phases" / "evaluate.yaml")
        assert _param_defaults(task["spec"])["openshell-user-fixture"] == ""
        assert _param_defaults(task["spec"])["agent-eval-harness-repo-revision"] == HARNESS_PIN
        openshell = next(step for step in task["spec"]["steps"] if step["name"] == "aeh-openshell-eval")

        env_from_secret_names = {
            source["secretRef"]["name"] for source in openshell.get("envFrom", []) if "secretRef" in source
        }
        env_secret_names = {
            env["valueFrom"]["secretKeyRef"]["name"]
            for env in openshell.get("env", [])
            if "secretKeyRef" in env.get("valueFrom", {})
        }
        assert "inference" not in env_from_secret_names | env_secret_names

        for name in ("INFERENCE_API_KEY", "OPENAI_API_KEY"):
            env = next(item for item in openshell["env"] if item["name"] == name)
            assert env["value"] == "$(params.llm-api-key)"
            assert "valueFrom" not in env

    def test_forge_briefing_has_installation_user_fixture(self):
        fixture = REPO / "submissions" / "openclaw-forge" / "fixtures" / "USER.md"
        fields = {}
        for line in fixture.read_text().splitlines():
            if line.startswith("- ") and ": " in line:
                key, value = line[2:].split(": ", 1)
                fields[key.lower()] = value.strip()
        assert all(fields.get(key) and not fields[key].startswith("<") for key in ("display name", "role", "initials"))
        scene = _load(REPO / "submissions" / "openclaw-forge" / "scenes" / "monday-acquisition.yaml")
        assert fields["primary email"] == scene["m365"]["user"]

        pipeline = _load(PIPELINE)
        evaluate = next(item for item in pipeline["spec"]["tasks"] if item["name"] == "evaluate")
        forwarded = {item["name"]: item["value"] for item in evaluate["params"]}
        assert forwarded["openshell-user-fixture"] == "$(params.openshell-user-fixture)"
        example = _load(REPO / "pipeline" / "runs" / "openshell-openclaw-pipelinerun.yaml")
        example_params = {item["name"]: item["value"] for item in example["spec"]["params"]}
        assert example_params["openshell-user-fixture"] == "fixtures/USER.md"

        task = _load(REPO / "pipeline" / "tasks" / "phases" / "evaluate.yaml")
        step = next(s for s in task["spec"]["steps"] if s["name"] == "aeh-openshell-eval")
        assert next(env for env in step["env"] if env["name"] == "OPENSHELL_USER_FIXTURE")["value"] == (
            "$(params.openshell-user-fixture)"
        )
        script = step["script"]
        assert 'FIXTURE_FILE="$SUBMISSION_DIR/$OPENSHELL_USER_FIXTURE"' in script
        assert "Using configured installation profile fixture" in script
        assert "fixtures/USER.md" not in script
        assert script.index('export AGENT_EVAL_FORGE_USER_FILE="$FIXTURE_FILE"') < script.index("scripts/run_aeh.py")

    def test_harbor_profiles_still_include_test(self):
        for path in (CI, CI_DEV):
            names = _task_names(_load(path)["spec"])
            assert "test" in names
            assert names[0] == "prepare"
