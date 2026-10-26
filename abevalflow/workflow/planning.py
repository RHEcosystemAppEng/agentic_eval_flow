"""Side-effect-free workflow planning and configuration redaction."""

from __future__ import annotations

from abevalflow.config.runtime import RuntimeConfig


def public_config(config: RuntimeConfig) -> dict:
    """Export reproducible configuration without credentials."""
    data = config.model_dump(mode="json")
    metadata = data.get("metadata")
    if metadata and metadata.get("gate_policy") and metadata["gate_policy"].get("push_facts"):
        metadata["gate_policy"]["push_facts"]["bearer_token"] = "${COMPASS_API_TOKEN}"
    return data


def build_plan(config: RuntimeConfig) -> dict:
    """Describe the requested operations without staging files or contacting services."""
    steps = ["prepare"]
    if 1 in config.tiers:
        steps += ["structure", "security", "quality"]
    if config.skill_review:
        steps += ["skill-review"]
    if 2 in config.tiers:
        steps += ["submission"]
        if config.submission_review:
            steps += ["submission-review"]
        steps += ["evaluate"]
    steps += ["report"]
    return {"config": public_config(config), "steps": steps, "publish": list(config.persist)}
