"""Resolve submission defaults, runtime environment, and explicit invocation options."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from abevalflow.schemas import SubmissionMetadata


class ConfigurationError(ValueError):
    """The requested workflow cannot be configured."""


class RuntimeConfig(BaseModel):
    """Resolved inputs for a single isolated evaluation run."""

    model_config = ConfigDict(extra="forbid")
    skill: Path
    source: Path
    submission: Path | None = None
    metadata: SubmissionMetadata | None = None
    tiers: tuple[int, ...] = (1,)
    output: Path
    run_id: str
    revision: str = "unknown"
    submission_review: bool = False
    skill_review: bool = False
    continue_on_tier1_failure: bool = False
    persist: tuple[str, ...] = ()
    persistence_required: bool = True
    backend: Literal["kubernetes"] = "kubernetes"
    kubeconfig: Path | None = None
    kube_context: str | None = None
    namespace: str = "default"
    credentials_secret: str = "llm-credentials"
    trial_image: str | None = None
    model: str | None = None
    judge_model: str | None = None
    score_script: Path | None = None
    aeh_root: Path | None = None
    aeh_python: Path | None = None
    scanner_runtime: Literal["local", "podman", "docker"] = "local"
    security_image: str | None = None
    quality_image: str | None = None
    security_command: str = "skill-scanner"
    quality_command: str = "harness-eval"
    command_timeout: int = Field(default=7200, gt=0)
    timeout_retries: int = Field(default=1, ge=0, le=5)
    insecure_ssl: bool = False
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = Field(default=None, exclude=True)
    agent_base_url: str | None = None
    judge_base_url: str | None = None
    judge_api_key: SecretStr | None = Field(default=None, exclude=True)
    agent_api_key: SecretStr | None = Field(default=None, exclude=True)
    database_tls_route_host: str | None = None
    database_url: SecretStr | None = Field(default=None, exclude=True)
    s3_endpoint: str | None = None
    s3_bucket: str = "ab-eval-reports"
    s3_access_key: SecretStr | None = Field(default=None, exclude=True)
    s3_secret_key: SecretStr | None = Field(default=None, exclude=True)
    s3_verify_tls: bool = True
    compass_token: SecretStr | None = Field(default=None, exclude=True)
    origins: dict[str, str] = Field(default_factory=dict)

    @field_validator("run_id")
    @classmethod
    def valid_run_id(cls, value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}", value):
            raise ValueError("run-id must be a filesystem-safe identifier of at most 101 characters")
        return value

    @property
    def name(self) -> str:
        return self.metadata.name if self.metadata else self.skill.name


def boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if str(value).lower() in {"true", "1", "yes", "on"}:
        return True
    if str(value).lower() in {"false", "0", "no", "off"}:
        return False
    raise ConfigurationError(f"Expected a boolean, received {value!r}")


def load_metadata(path: Path) -> SubmissionMetadata:
    try:
        raw = yaml.safe_load((path / "metadata.yaml").read_text())
        if not isinstance(raw, dict):
            raise ValueError("metadata.yaml must be a mapping")
        return SubmissionMetadata.model_validate(raw)
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors())
        raise ConfigurationError(f"Invalid submission {path}; invalid fields: {fields}") from exc
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Invalid submission {path} ({type(exc).__name__})") from exc


def parse_tiers(value: str) -> tuple[int, ...]:
    try:
        tiers = tuple(sorted(set(int(v) for v in str(value).split(","))))
    except ValueError as exc:
        raise ConfigurationError("--tiers must be 1, 2, or 1,2") from exc
    if not tiers or any(v not in (1, 2) for v in tiers):
        raise ConfigurationError("--tiers must be 1, 2, or 1,2")
    return tiers


def parse_destinations(value: str) -> tuple[str, ...]:
    destinations = tuple(dict.fromkeys(v.strip() for v in value.split(",") if v.strip()))
    if set(destinations) - {"postgres", "s3", "compass"}:
        raise ConfigurationError("Persistence destinations: postgres,s3,compass")
    return destinations


def resolve_config(options: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> RuntimeConfig:
    """Resolve explicit flags and environment while preserving submission-owned defaults."""
    env = os.environ if environ is None else environ
    origins: dict[str, str] = {}

    def get(name: str, default: Any = None, *aliases: str) -> Any:
        if options.get(name) is not None:
            origins[name] = "flag"
            return options[name]
        for key in (f"ABEVAL_{name.upper()}", *aliases):
            if env.get(key):
                origins[name] = key
                return env[key]
        origins[name] = "default"
        return default

    skill_id = get("skill_id")
    source_option = get("source")
    skill_option = get("skill", ".")
    if skill_id:
        if options.get("skill") is not None:
            raise ConfigurationError("Choose a skill path or --skill-id")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*/[a-zA-Z0-9][a-zA-Z0-9_.-]*", skill_id):
            raise ConfigurationError("skill-id must be <pack>/<skill>")
        source = Path(source_option or ".").expanduser().resolve()
        pack, name = skill_id.split("/")
        skill = (source / pack / "skills" / name).resolve()
        origins["skill"] = origins["skill_id"]
    else:
        skill = Path(skill_option).expanduser().resolve()
        source = (
            Path(source_option).expanduser().resolve()
            if source_option
            else next((p for p in (skill, *skill.parents) if (p / ".git").exists()), skill)
        )
    if not (skill / "SKILL.md").is_file():
        raise ConfigurationError(f"Skill directory must contain SKILL.md: {skill}")
    if not skill.is_relative_to(source):
        raise ConfigurationError("Skill must be inside --source")
    submission_option = get("submission")
    submission = Path(submission_option).expanduser().resolve() if submission_option else None
    if submission is None:
        relative = skill.relative_to(source)
        if "skills" in relative.parts:
            parts = list(relative.parts)
            parts.remove("skills")
            candidate = source / "submissions" / Path(*parts)
            if (candidate / "metadata.yaml").is_file():
                submission = candidate
    if submission and not submission.is_relative_to(source):
        raise ConfigurationError("Submission must be inside --source; select a common source root")
    metadata = load_metadata(submission) if submission else None
    tiers = parse_tiers(get("tiers", "1"))
    if 2 in tiers and submission is None:
        raise ConfigurationError("Tier 2 requires an AEH submission; supply --submission")
    from datetime import UTC, datetime
    from uuid import uuid4

    run_id = get("run_id", f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}")
    output = Path(get("output", Path.cwd() / ".abeval" / "runs" / run_id)).expanduser().resolve()
    if output == source or output.is_relative_to(skill) or (submission and output.is_relative_to(submission)):
        raise ConfigurationError("Output must be outside the skill and submission directories")
    review = get("submission_review", "auto")
    if review not in {"auto", "on", "off"}:
        raise ConfigurationError("submission-review must be auto, on, or off")
    review_enabled = 2 in tiers and (review == "on" or (review == "auto" and not metadata.skip_quality_review))
    if review == "auto" and metadata:
        origins["submission_review"] = "metadata.skip_quality_review"
    if metadata and metadata.gate_policy and not review_enabled and 2 in tiers:
        if metadata.gate_policy.get_gate_policy("quality").mode.value == "block":
            raise ConfigurationError("Blocking quality policy requires submission review")
    destinations = parse_destinations(get("persist", ""))
    values: dict[str, Any] = {
        "skill": skill,
        "source": source,
        "submission": submission,
        "metadata": metadata,
        "tiers": tiers,
        "output": output,
        "run_id": run_id,
        "submission_review": review_enabled,
        "persist": destinations,
    }
    defaults = {
        "revision": ("unknown", "SUBMISSION_REVISION"),
        "skill_review": (False,),
        "continue_on_tier1_failure": (False,),
        "persistence_required": (True,),
        "backend": ("kubernetes",),
        "kubeconfig": (None, "KUBECONFIG"),
        "kube_context": (None,),
        "namespace": ("default", "AGENT_EVAL_K8S_NAMESPACE"),
        "credentials_secret": ("llm-credentials", "AGENT_EVAL_K8S_CREDENTIALS_SECRET"),
        "trial_image": (None, "AEH_IMAGE"),
        "model": (None,),
        "judge_model": (None,),
        "score_script": (None, "AEH_SCORE_PY"),
        "aeh_root": (None,),
        "aeh_python": (None,),
        "security_command": ("skill-scanner",),
        "quality_command": ("harness-eval",),
        "scanner_runtime": ("local",),
        "security_image": (None,),
        "quality_image": (None,),
        "command_timeout": (7200,),
        "timeout_retries": (1,),
        "insecure_ssl": (False, "AEH_INSECURE_SSL"),
        "llm_base_url": (None, "LLM_BASE_URL", "LLM_API_BASE"),
        "llm_model": (None, "LLM_MODEL"),
        "llm_api_key": (None, "LLM_API_KEY"),
        "agent_base_url": (None, "ANTHROPIC_BASE_URL"),
        "agent_api_key": (None, "ANTHROPIC_API_KEY", "OPENAI_API_KEY"),
        "judge_base_url": (None, "OPENAI_BASE_URL"),
        "judge_api_key": (None, "OPENAI_API_KEY"),
        "database_url": (None, "DATABASE_URL"),
        "database_tls_route_host": (None, "DB_TLS_ROUTE_HOST"),
        "s3_endpoint": (None, "MINIO_ENDPOINT"),
        "s3_bucket": ("ab-eval-reports",),
        "s3_access_key": (None, "MINIO_ACCESS_KEY", "AWS_ACCESS_KEY_ID"),
        "s3_secret_key": (None, "MINIO_SECRET_KEY", "AWS_SECRET_ACCESS_KEY"),
        "s3_verify_tls": (True,),
        "compass_token": (None, "COMPASS_API_TOKEN"),
    }
    for name, (default, *aliases) in defaults.items():
        values[name] = get(name, default, *aliases)
    for name in ("skill_review", "continue_on_tier1_failure", "persistence_required", "s3_verify_tls", "insecure_ssl"):
        values[name] = boolean(values[name])
    for name in ("kubeconfig", "score_script", "aeh_root"):
        if values[name]:
            values[name] = Path(values[name]).expanduser().resolve()
    if values["aeh_python"]:
        values["aeh_python"] = Path(values["aeh_python"]).expanduser().absolute()
    if values["kube_context"] and not values["kubeconfig"]:
        candidate = Path.home() / ".kube/config"
        if candidate.is_file():
            values["kubeconfig"] = candidate
        else:
            raise ConfigurationError("--kube-context requires a kubeconfig")
    if origins["namespace"] == "default":
        kube_path = values["kubeconfig"]
        if kube_path is None and not env.get("KUBERNETES_SERVICE_HOST"):
            candidate = Path.home() / ".kube/config"
            kube_path = candidate if candidate.is_file() else None
        if kube_path and kube_path.is_file():
            raw = yaml.safe_load(kube_path.read_text()) or {}
            context = values["kube_context"] or raw.get("current-context")
            selected = next((c.get("context", {}) for c in raw.get("contexts", []) if c.get("name") == context), {})
            values["namespace"] = selected.get("namespace", "default")
        elif env.get("KUBERNETES_SERVICE_HOST"):
            namespace_file = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
            if namespace_file.is_file():
                values["namespace"] = namespace_file.read_text().strip()
    if origins["s3_verify_tls"] == "default" and env.get("MINIO_INSECURE_SSL"):
        values["s3_verify_tls"] = not boolean(env["MINIO_INSECURE_SSL"])
    if 1 in tiers and values["scanner_runtime"] in {"podman", "docker"}:
        missing = [f"--{name}-image" for name in ("security", "quality") if not values[f"{name}_image"]]
        if missing:
            raise ConfigurationError("Container scanning requires " + " and ".join(missing))
    values["origins"] = origins
    return RuntimeConfig.model_validate(values)


def refresh_credentials(config: RuntimeConfig, environ: Mapping[str, str] | None = None) -> RuntimeConfig:
    """Restore secrets without re-reading or changing the recorded evaluation inputs."""
    env = os.environ if environ is None else environ
    aliases = {
        "llm_api_key": ("LLM_API_KEY",),
        "agent_api_key": ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"),
        "judge_api_key": ("OPENAI_API_KEY",),
        "database_url": ("DATABASE_URL",),
        "s3_access_key": ("MINIO_ACCESS_KEY", "AWS_ACCESS_KEY_ID"),
        "s3_secret_key": ("MINIO_SECRET_KEY", "AWS_SECRET_ACCESS_KEY"),
        "compass_token": ("COMPASS_API_TOKEN",),
    }
    values = config.model_dump()
    for field, names in aliases.items():
        value = next((env[key] for key in (f"ABEVAL_{field.upper()}", *names) if env.get(key)), None)
        values[field] = value
    publication_aliases = {
        "database_tls_route_host": ("DB_TLS_ROUTE_HOST",),
        "s3_endpoint": ("MINIO_ENDPOINT",),
        "s3_bucket": (),
        "s3_verify_tls": (),
        "persistence_required": (),
    }
    for field, names in publication_aliases.items():
        value = next((env[key] for key in (f"ABEVAL_{field.upper()}", *names) if env.get(key)), None)
        if value is not None:
            values[field] = boolean(value) if field in {"s3_verify_tls", "persistence_required"} else value
    if env.get("MINIO_INSECURE_SSL") and not env.get("ABEVAL_S3_VERIFY_TLS"):
        values["s3_verify_tls"] = not boolean(env["MINIO_INSECURE_SSL"])
    return RuntimeConfig.model_validate(values)
