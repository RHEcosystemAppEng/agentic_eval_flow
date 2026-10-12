"""Tests for the OpenShell OpenClaw pipeline profile."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
PIPELINE = REPO / "pipeline" / "pipelines" / "ci-pipeline-openshell.yaml"
HARNESS_PIN = "8d58e500d0eb55a3106819ccacf26da6bf7ea166"
CI = REPO / "pipeline" / "pipelines" / "ci-pipeline.yaml"
CI_DEV = REPO / "pipeline" / "pipelines" / "ci-pipeline-dev.yaml"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _param_defaults(spec: dict) -> dict[str, str | None]:
    return {p["name"]: p.get("default") for p in spec["params"]}


def _task_names(spec: dict) -> list[str]:
    return [t["name"] for t in spec["tasks"]]


class TestOpenshellPipelineProfile:
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

    def test_cleanup_task_is_best_effort(self):
        task = _load(REPO / "pipeline" / "tasks" / "post" / "cleanup_pvc.yaml")
        step = task["spec"]["steps"][0]
        assert step.get("onError") == "continue"
        assert "--request-timeout=20s" in step["script"]
        assert step["image"].startswith("image-registry.openshift-image-registry.svc:5000/")

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
        # TLS chain validation is enabled only when the CA Secret exists.
        ca = next(e for e in step["env"] if e["name"] == "GATEWAY_CA_CERT")
        ref = ca["valueFrom"]["secretKeyRef"]
        assert ref["name"] == "forge-ai-gateway-ca"
        assert ref["optional"] is True

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
