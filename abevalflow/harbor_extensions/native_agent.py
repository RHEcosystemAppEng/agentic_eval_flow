"""Trusted native-agent selection for managed Harbor MCP comparisons."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

import tomlkit

CODEX_VERSION = "0.169.0"
ENMAAS_BASE_URL = "https://api.enmaas.devshift.net/v1"


def _stage_codex_context(task_dir):
    """Expose the same common context through Codex's native discovery file."""
    environment = Path(task_dir) / "environment"
    source = environment / "CLAUDE.md"
    destination = environment / "AGENTS.md"
    if source.is_file():
        context = source.read_bytes()
        if destination.exists() and destination.read_bytes() != context:
            raise ValueError("Codex AGENTS.md conflicts with the shared CLAUDE.md context")
        destination.write_bytes(context)
    elif destination.exists():
        raise ValueError("Codex AGENTS.md has no matching shared CLAUDE.md context")

    # Harbor injects environment.skills_dir into the native agent constructor;
    # its Codex adapter registers those files under $HOME/.agents/skills.
    skills = environment / "skills"
    if not any(skills.rglob("SKILL.md")):
        return
    path = Path(task_dir) / "task.toml"
    document = tomlkit.parse(path.read_text())
    options = document.setdefault("environment", tomlkit.table())
    workdir = PurePosixPath(options.get("workdir") or "/workspace")
    if not workdir.is_absolute() or ".." in workdir.parts:
        raise ValueError("Codex managed tasks require an absolute normalized workdir")
    expected = str(workdir / "skills")
    if options.get("skills_dir") not in (None, expected):
        raise ValueError("Codex skills_dir must match the staged environment skills")
    options["skills_dir"] = expected
    path.write_text(tomlkit.dumps(document))


def configure_native_agent(
    config,
    task_dir,
    *,
    agent="claude-code",
    model="claude-sonnet",
    judge_model=None,
    judge_provider=None,
    agent_version=None,
    reasoning_effort="low",
    enmaas_base_url=ENMAAS_BASE_URL,
    judge_base_url=None,
):
    """Configure one native agent and an independently routed judge.

    Credentials remain environment references in persisted Harbor configs.
    The caller must provide the selected credential in its own environment.
    """
    if agent not in {"claude-code", "codex"}:
        raise ValueError("managed Harbor supports claude-code or codex")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("native model must be nonempty")
    judge_provider = judge_provider or ("enmaas" if agent == "codex" else "litellm")
    if judge_provider not in {"enmaas", "litellm"}:
        raise ValueError("judge provider must be enmaas or litellm")
    judge_model = judge_model or ("gpt-6.1-terra" if judge_provider == "enmaas" else "claude-sonnet")
    if not isinstance(judge_model, str) or not judge_model.strip():
        raise ValueError("judge model must be nonempty")
    if agent == "claude-code":
        native = {
            "name": agent,
            "model_name": model,
            "env": {
                "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
                "ANTHROPIC_BASE_URL": os.environ["ANTHROPIC_BASE_URL"],
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "DISABLE_AUTOUPDATER": "1",
            },
        }
        if agent_version:
            native["kwargs"] = {"version": agent_version}
    else:
        if reasoning_effort not in {"minimal", "low", "medium", "high", "xhigh"}:
            raise ValueError("invalid Codex reasoning effort")
        if enmaas_base_url != ENMAAS_BASE_URL:
            raise ValueError("Codex provider must use the trusted EnMaaS endpoint")
        _stage_codex_context(task_dir)
        native = {
            "name": "codex",
            "model_name": model,
            "env": {"ENMAAS_KEY": "${ENMAAS_KEY}"},
            "kwargs": {
                "version": agent_version or CODEX_VERSION,
                "reasoning_effort": reasoning_effort,
                "config": {
                    "model_provider": "enmaas",
                    "model_providers": {
                        "enmaas": {
                            "name": "EnMaaS",
                            "base_url": enmaas_base_url,
                            "wire_api": "responses",
                            "env_key": "ENMAAS_KEY",
                        }
                    },
                },
            },
        }
    config["agents"] = [native]
    if judge_provider == "enmaas":
        if judge_base_url not in (None, ENMAAS_BASE_URL):
            raise ValueError("judge must use the trusted EnMaaS endpoint")
        judge_env = {
            "ENMAAS_KEY": "${ENMAAS_KEY}",
            "JUDGE_API_KEY": "${ENMAAS_KEY}",
            "JUDGE_PROVIDER": "openai-responses",
            "JUDGE_MODEL": judge_model,
            "JUDGE_BASE_URL": ENMAAS_BASE_URL,
        }
    else:
        judge_env = {
            "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
            "JUDGE_MODEL": judge_model,
            "JUDGE_BASE_URL": judge_base_url or os.environ["ANTHROPIC_BASE_URL"],
        }
    config["verifier"] = {"env": judge_env}
    return config
