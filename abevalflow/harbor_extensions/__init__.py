"""OpenShift environment and task enrichment for AEH's Harbor backend.

The orchestrator image supplies Harbor >=0.21, the Kubernetes client, and
upstream agent_eval. Tasks use the separate prebuilt trial image through
task.toml's docker_image. See containers/ for the dependency contract.
"""

OPENSHIFT_ENVIRONMENT_IMPORT_PATH = "abevalflow.harbor_extensions.openshift_environment:OpenShiftEnvironment"

__all__ = ["OPENSHIFT_ENVIRONMENT_IMPORT_PATH"]
