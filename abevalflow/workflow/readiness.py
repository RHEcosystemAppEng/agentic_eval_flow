"""Capability-specific dependency and workload-cluster readiness checks."""

from __future__ import annotations

import importlib.util
import shutil

from abevalflow.config.runtime import RuntimeConfig


def check_readiness(config: RuntimeConfig, *, cluster: bool = True) -> list[dict]:
    """Inspect requested capabilities without executing models or creating trial pods."""
    checks: list[dict] = []

    def record(name: str, ready: bool, detail: str) -> None:
        checks.append({"name": name, "ready": ready, "detail": detail})

    if 1 in config.tiers:
        commands = (
            (config.security_command, config.quality_command)
            if config.scanner_runtime == "local"
            else (config.scanner_runtime,)
        )
        for command in commands:
            record(command, shutil.which(command) is not None, "Scanner executable must be on PATH")
    if config.skill_review or config.submission_review:
        record("review-client", importlib.util.find_spec("openai") is not None, "Install abevalflow[llm]")
        record(
            "review-endpoint",
            bool(config.llm_base_url and config.llm_model and config.llm_api_key),
            "Review requires LLM base URL, model, and API key",
        )
    if 2 in config.tiers:
        for module in ("harbor", "kubernetes"):
            record(module, importlib.util.find_spec(module) is not None, "Install abevalflow[runner]")
        from abevalflow.execution.runtime import RuntimeUnavailable, resolve_runtime

        try:
            runtime = resolve_runtime(config.aeh_root, config.aeh_python)
            record("aeh-runtime", True, str(runtime.root))
        except RuntimeUnavailable as exc:
            runtime = None
            record("aeh-runtime", False, str(exc))
        record("trial-image", bool(config.trial_image), "Supply --trial-image or ABEVAL_TRIAL_IMAGE")
        record("kubeconfig", not config.kubeconfig or config.kubeconfig.is_file(), "Explicit kubeconfig must exist")
        if config.metadata.aeh_mode.value == "pairwise":
            record(
                "pairwise-scorer",
                bool(runtime and (config.score_script or runtime.script("score.py")).is_file()),
                "Install the complete AEH runtime with 'abeval runtime install'",
            )
        if cluster and all(c["ready"] for c in checks if c["name"] in {"kubernetes", "kubeconfig"}):
            try:
                from kubernetes import client
                from kubernetes import config as kube

                if config.kubeconfig:
                    api = kube.new_client_from_config(str(config.kubeconfig), context=config.kube_context)
                else:
                    try:
                        kube.load_incluster_config()
                    except kube.ConfigException:
                        kube.load_kube_config(context=config.kube_context)
                    api = client.ApiClient()
                auth = client.AuthorizationV1Api(api)
                for resource, verbs in {
                    "pods": ("create", "get", "list", "watch", "delete"),
                    "pods/exec": ("create",),
                    "pods/log": ("get",),
                }.items():
                    for verb in verbs:
                        root, _, subresource = resource.partition("/")
                        review = client.V1SelfSubjectAccessReview(
                            spec=client.V1SelfSubjectAccessReviewSpec(
                                resource_attributes=client.V1ResourceAttributes(
                                    namespace=config.namespace,
                                    verb=verb,
                                    resource=root,
                                    subresource=subresource or None,
                                )
                            )
                        )
                        allowed = auth.create_self_subject_access_review(review, _request_timeout=10).status.allowed
                        record(f"rbac:{verb}:{resource}", bool(allowed), config.namespace)
                client.CoreV1Api(api).read_namespaced_secret(
                    config.credentials_secret, config.namespace, _request_timeout=10
                )
                record("workload-secret", True, f"{config.namespace}/{config.credentials_secret}")
                api.close()
            except Exception as exc:
                record("cluster", False, f"Cluster readiness failed ({type(exc).__name__})")
    if "postgres" in config.persist:
        record("postgres-config", bool(config.database_url), "Supply ABEVAL_DATABASE_URL or DATABASE_URL")
        record("postgres-client", importlib.util.find_spec("sqlalchemy") is not None, "Install abevalflow[storage]")
    if "s3" in config.persist:
        record(
            "s3-config",
            bool(config.s3_endpoint and config.s3_access_key and config.s3_secret_key),
            "Supply S3 endpoint, access key, and secret key",
        )
        record("s3-client", importlib.util.find_spec("minio") is not None, "Install abevalflow[storage]")
    if "compass" in config.persist:
        record(
            "compass-config",
            bool(config.metadata and config.metadata.gate_policy and config.metadata.gate_policy.push_facts),
            "Submission must configure gate_policy.push_facts",
        )
    return checks
