"""Trusted routing and equivalent context for native Harbor agents."""

import json

import pytest
import tomlkit

from abevalflow.harbor_extensions.native_agent import CODEX_VERSION, ENMAAS_BASE_URL, configure_native_agent


def task(tmp_path, *, skills=False):
    environment = tmp_path / "environment"
    environment.mkdir()
    (environment / "CLAUDE.md").write_bytes(b"Shared context.\r\n")
    (tmp_path / "task.toml").write_text('[environment]\nworkdir="/workspace"\n')
    if skills:
        skill = environment / "skills/create/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_bytes(b"Unchanged skill bytes.\n")
    return tmp_path


def test_default_claude_keeps_existing_agent_and_sonnet_judge(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://litellm:4000")
    root = task(tmp_path)
    config = configure_native_agent({}, root)
    agent = config["agents"][0]
    assert agent["name"] == "claude-code"
    assert agent["model_name"] == "claude-sonnet"
    assert agent["env"]["ANTHROPIC_API_KEY"] == "${ANTHROPIC_API_KEY}"
    assert agent["env"]["ANTHROPIC_BASE_URL"] == "http://litellm:4000"
    assert config["verifier"]["env"] == {
        "ANTHROPIC_API_KEY": "${ANTHROPIC_API_KEY}",
        "JUDGE_MODEL": "claude-sonnet",
        "JUDGE_BASE_URL": "http://litellm:4000",
    }
    assert not (root / "environment/AGENTS.md").exists()


def test_codex_terra_and_judge_use_enmaas_without_anthropic_credentials(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setenv("ENMAAS_KEY", "secret-that-must-not-be-serialized")
    root = task(tmp_path, skills=True)
    before = (root / "environment/skills/create/SKILL.md").read_bytes()
    config = configure_native_agent({}, root, agent="codex", model="openai/gpt-6.1-terra")
    agent = config["agents"][0]
    assert agent["name"] == "codex" and agent["model_name"] == "openai/gpt-6.1-terra"
    assert agent["env"] == {"ENMAAS_KEY": "${ENMAAS_KEY}"}
    assert agent["kwargs"]["version"] == CODEX_VERSION
    assert agent["kwargs"]["reasoning_effort"] == "low"
    assert agent["kwargs"]["config"] == {
        "model_provider": "enmaas",
        "model_providers": {
            "enmaas": {"name": "EnMaaS", "base_url": ENMAAS_BASE_URL, "wire_api": "responses", "env_key": "ENMAAS_KEY"}
        },
    }
    assert config["verifier"]["env"] == {
        "ENMAAS_KEY": "${ENMAAS_KEY}",
        "JUDGE_API_KEY": "${ENMAAS_KEY}",
        "JUDGE_PROVIDER": "openai-responses",
        "JUDGE_MODEL": "gpt-6.1-terra",
        "JUDGE_BASE_URL": ENMAAS_BASE_URL,
    }
    assert "secret-that-must-not-be-serialized" not in json.dumps(config)
    assert (root / "environment/AGENTS.md").read_bytes() == (root / "environment/CLAUDE.md").read_bytes()
    assert (root / "environment/skills/create/SKILL.md").read_bytes() == before
    assert tomlkit.parse((root / "task.toml").read_text())["environment"]["skills_dir"] == "/workspace/skills"


def test_codex_control_has_no_staged_skill(tmp_path):
    root = task(tmp_path)
    configure_native_agent({}, root, agent="codex", model="openai/gpt-6.1-terra")
    assert "skills_dir" not in tomlkit.parse((root / "task.toml").read_text())["environment"]
    assert not (root / "environment/skills").exists()


def test_codex_rejects_different_common_context(tmp_path):
    root = task(tmp_path)
    (root / "environment/AGENTS.md").write_text("Different instructions")
    with pytest.raises(ValueError, match="conflicts"):
        configure_native_agent({}, root, agent="codex")


def test_codex_preserves_matching_common_context_and_is_idempotent(tmp_path):
    root = task(tmp_path, skills=True)
    first = configure_native_agent({}, root, agent="codex")
    assert configure_native_agent({}, root, agent="codex") == first


def test_codex_rejects_unrepresented_context(tmp_path):
    root = task(tmp_path)
    (root / "environment/CLAUDE.md").unlink()
    (root / "environment/AGENTS.md").write_text("Additional instructions")
    with pytest.raises(ValueError, match="no matching"):
        configure_native_agent({}, root, agent="codex")


def test_codex_rejects_wrong_skill_directory(tmp_path):
    root = task(tmp_path, skills=True)
    (root / "task.toml").write_text('[environment]\nworkdir="/workspace"\nskills_dir="/hidden"\n')
    with pytest.raises(ValueError, match="skills_dir"):
        configure_native_agent({}, root, agent="codex")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"agent": "arbitrary-code"},
        {"agent": "codex", "reasoning_effort": "unbounded"},
        {"agent": "codex", "enmaas_base_url": "https://other.invalid"},
        {"agent": "codex", "judge_base_url": "https://other.invalid"},
        {"judge_provider": "unknown"},
        {"model": ""},
    ],
)
def test_invalid_native_configuration_rejected(tmp_path, kwargs):
    with pytest.raises(ValueError):
        configure_native_agent({}, task(tmp_path), **kwargs)


def test_codex_can_explicitly_retain_separate_litellm_judge(tmp_path):
    config = configure_native_agent(
        {},
        task(tmp_path),
        agent="codex",
        judge_provider="litellm",
        judge_base_url="http://litellm:4000",
        judge_model="other-judge",
    )
    assert config["verifier"]["env"]["JUDGE_MODEL"] == "other-judge"
    assert "ANTHROPIC_API_KEY" in config["verifier"]["env"]
