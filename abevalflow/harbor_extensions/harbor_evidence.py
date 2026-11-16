"""Validate archived native Harbor producers, observations and score agreement."""

from __future__ import annotations

import json
from collections import Counter


def validate_attempt(objects, uid, variant, mock_name, require_cleanup=True):
    def one(suffix, as_json=True):
        values = [v for k, v in objects.items() if k.endswith("/" + suffix)]
        if len(values) != 1 or not values[0]:
            raise RuntimeError("missing or ambiguous Harbor evidence: " + suffix)
        return json.loads(values[0]) if as_json else values[0].decode()

    inventory = one("attempt-inventory.json")
    target = one("target.json")
    ready = one("readiness.json")
    if inventory.get("uid") != uid or inventory.get("variant") != variant or inventory.get("mock") != mock_name:
        raise RuntimeError("invalid Harbor producer inventory identity")
    pods = inventory.get("pods", [])
    client = inventory.get("client")
    if len(pods) != 2 or {p.get("name") for p in pods} != {mock_name, client} or not client or client == mock_name:
        raise RuntimeError("Harbor requires exact mock and client inventory")
    if len({p.get("uid") for p in pods}) != 2 or not all(p.get("uid") for p in pods):
        raise RuntimeError("Harbor producer Pod UIDs must be present and distinct")
    if target.get("uid") != uid or target.get("name") != mock_name or not target.get("mock"):
        raise RuntimeError("invalid Harbor target identity")
    if (
        ready.get("uid") != uid
        or ready.get("name") != mock_name
        or ready.get("endpoint") != target.get("endpoint")
        or ready.get("ready") is not True
        or not ready.get("tools")
    ):
        raise RuntimeError("invalid Harbor MCP readiness identity")
    for pod in pods:
        name = pod["name"]
        status = one(f"pod-status/{name}.json")
        if (
            status.get("name") != name
            or status.get("pod_uid") != pod["uid"]
            or status.get("labels", {}).get("abevalflow.io/mcp-run") != uid
        ):
            raise RuntimeError("archived Pod identity does not match inventory")
        if status.get("status", {}).get("phase") not in {"Succeeded", "Failed"}:
            raise RuntimeError("archived Pod must be terminal")
        image = target["descriptor"]["image"] if name == mock_name else inventory["client_image"]
        if "@sha256:" not in image or image not in status.get("images", []):
            raise RuntimeError("archived producer image does not match pinned image")
        identities = [s.get("image_id", "") for s in status["status"].get("container_statuses", [])]
        if not any(s.endswith(image.split("@", 1)[1]) for s in identities):
            raise RuntimeError("archived runtime image digest mismatch")
        one(f"pod-logs/{name}.log", False)
    audit = []
    for line in one(f"pod-logs/{mock_name}.log", False).splitlines():
        # Kubernetes timestamp prefixes precede stdout JSON records.
        start = line.find("{")
        if start < 0:
            continue
        try:
            event = json.loads(line[start:])
        except ValueError:
            continue
        if event.get("event") == "tool_call":
            audit.append(event)
    behavior = one("behavior.json")
    judge = one("answer-judge.json")
    if not isinstance(behavior.get("passed"), bool) or not isinstance(judge.get("passed"), bool):
        raise RuntimeError("missing completed behavior or answer assessment")
    if not audit or Counter((e["tool"], e["outcome"]) for e in audit) != Counter(
        (e["tool"], e["outcome"]) for e in behavior.get("calls", [])
    ):
        raise RuntimeError("native MCP observations disagree with trusted mock audit")
    if not one("trajectory.json"):
        raise RuntimeError("empty native trajectory")
    trials = []
    for key, value in objects.items():
        if key.endswith("/result.json"):
            item = json.loads(value)
            if "verifier_result" in item:
                trials.append(item)
    if len(trials) != 1 or trials[0].get("exception_info"):
        raise RuntimeError("missing or failed native trial")
    rewards = trials[0]["verifier_result"] or {}
    reward = rewards.get("rewards", {}).get("reward", rewards.get("reward"))
    if reward != float(behavior["passed"] and judge["passed"]):
        raise RuntimeError("native reward disagrees with behavior and judge")
    if require_cleanup:
        cleanup = one("cleanup.json")
        if cleanup != {"uid": uid, "variant": variant, "deleted": True, "pods": pods}:
            raise RuntimeError("missing exact producer deletion confirmation")
    return inventory
