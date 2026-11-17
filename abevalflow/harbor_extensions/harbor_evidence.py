"""Validate archived native Harbor producers, observations and score agreement."""

from __future__ import annotations

import json
import math
from collections import Counter


def bounded_score(value, label):
    """Reject booleans, nonfinite values and unbounded scores at the archive boundary."""
    if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
        raise RuntimeError("invalid bounded numeric score: " + label)
    return float(value)


def scoring_version(document):
    if not isinstance(document, dict):
        raise RuntimeError("assessment must be a JSON object")
    if "scoring_version" not in document:
        return None
    if type(document["scoring_version"]) is not int or document["scoring_version"] != 2:
        raise RuntimeError("unsupported Harbor scoring version")
    return 2


def validate_judge(judge):
    """Support explicitly versioned numeric judgments and historical boolean judgments."""
    version = scoring_version(judge)
    if version is None:
        if any(key in judge for key in ("score", "overall_score", "weights", "llm_judgment", "findings")):
            raise RuntimeError("numeric judge fields require scoring_version=2")
        if type(judge.get("passed")) is not bool:
            raise RuntimeError("missing completed answer assessment")
        return float(judge["passed"])
    score = bounded_score(judge.get("score"), "LLM judgment")
    if type(judge.get("passed")) is not bool or judge["passed"] != (score == 1):
        raise RuntimeError("LLM judgment pass flag disagrees with score")
    if (
        not isinstance(judge.get("reason"), str)
        or not judge["reason"].strip()
        or not isinstance(judge.get("findings"), list)
    ):
        raise RuntimeError("missing completed semantic LLM judgment")
    for finding in judge["findings"]:
        if not isinstance(finding, dict) or not all(
            isinstance(finding.get(k), str) and finding[k].strip() for k in ("issue", "evidence")
        ):
            raise RuntimeError("invalid semantic LLM finding")
        if bounded_score(finding.get("deduction"), "finding deduction") == 0:
            raise RuntimeError("semantic LLM deduction must be positive")
    if (score == 1) != (not judge["findings"]):
        raise RuntimeError("semantic LLM findings disagree with perfect/nonperfect score")
    deductions = math.fsum(finding["deduction"] for finding in judge["findings"])
    if deductions > 1 + 1e-6 or not math.isclose(score, 1 - deductions, rel_tol=0, abs_tol=1e-6):
        raise RuntimeError("semantic LLM score disagrees with itemized deductions")
    return score


def _same_score(actual, expected, label):
    if not math.isclose(bounded_score(actual, label), expected, rel_tol=0, abs_tol=1e-9):
        raise RuntimeError("inconsistent Harbor score: " + label)


def _category(category, label):
    if not isinstance(category, dict):
        raise RuntimeError("missing Harbor scoring category: " + label)
    checks = category.get("checks")
    if (
        not isinstance(checks, dict)
        or not checks
        or not all(isinstance(k, str) and type(v) is bool for k, v in checks.items())
    ):
        raise RuntimeError("invalid boolean category checks: " + label)
    expected = sum(checks.values()) / len(checks)
    _same_score(category.get("score"), expected, label)
    if type(category.get("passed")) is not bool or category["passed"] != (expected == 1):
        raise RuntimeError("category pass flag disagrees with checks: " + label)
    return expected


def validate_assessments(behavior, judge, breakdown=None):
    """Recompute the declared score without silently upgrading or downgrading schemas."""
    versions = [scoring_version(behavior), scoring_version(judge)]
    if breakdown is not None:
        versions.append(scoring_version(breakdown))
    judge_score = validate_judge(judge)
    if any(v == 2 for v in versions):
        if breakdown is None or any(v != 2 for v in versions):
            raise RuntimeError("mixed or incomplete versioned Harbor assessments")
        categories = ("task_completion", "protocol_compliance")
        scores = [_category(behavior.get(key), key) for key in categories]
        expected_behavior = sum(scores) / 2
        _same_score(behavior.get("score"), expected_behavior, "behavior")
        if type(behavior.get("passed")) is not bool or behavior["passed"] != (expected_behavior == 1):
            raise RuntimeError("behavior pass flag disagrees with score")
        if not isinstance(behavior.get("failures"), list) or breakdown.get("failures") != behavior["failures"]:
            raise RuntimeError("breakdown failures disagree with behavior")
        for key in categories:
            _category(breakdown.get(key), "breakdown " + key)
            if breakdown[key] != behavior[key]:
                raise RuntimeError("breakdown category disagrees with behavior")
        # Dict equality alone treats booleans as numbers and 2.0 as integer 2.
        # Revalidate nested types/version before comparing the recorded copy.
        validate_judge(breakdown.get("llm_judgment"))
        if breakdown.get("llm_judgment") != judge:
            raise RuntimeError("breakdown LLM judgment disagrees with native judgment")
        weights = breakdown.get("weights")
        if not isinstance(weights, dict) or set(weights) != {*categories, "llm_judgment"}:
            raise RuntimeError("invalid Harbor category weights")
        for key, value in weights.items():
            _same_score(value, 1 / 3, key + " weight")
        expected = sum([*scores, judge_score]) / 3
        _same_score(breakdown.get("overall_score"), expected, "overall")
        if type(breakdown.get("overall_passed")) is not bool or breakdown["overall_passed"] != (expected == 1):
            raise RuntimeError("overall pass flag disagrees with score")
        return expected
    # Historical assessments may include boolean category breakdowns, but no
    # numeric scored fields without the explicit version marker.
    for document in [behavior, *([breakdown] if breakdown is not None else [])]:
        if any(key in document for key in ("score", "overall_score", "weights", "llm_judgment")):
            raise RuntimeError("numeric assessment fields require scoring_version=2")
        for key in ("task_completion", "protocol_compliance", "answer_accuracy"):
            category = document.get(key)
            if isinstance(category, dict) and ("score" in category or "scoring_version" in category):
                raise RuntimeError("numeric category fields require scoring_version=2")
    if type(behavior.get("passed")) is not bool:
        raise RuntimeError("missing completed behavior assessment")
    return float(behavior["passed"] and bool(judge_score))


def validate_judge_input(payload, read_trace):
    """Check the saved full-trace request structurally, without grading its text."""
    if not isinstance(payload, dict) or set(payload) != {"model", "temperature", "messages"}:
        raise RuntimeError("invalid full-trace judge request")
    if not isinstance(payload["model"], str) or not payload["model"].strip():
        raise RuntimeError("missing semantic judge model route")
    if type(payload["temperature"]) not in (int, float) or payload["temperature"] != 0:
        raise RuntimeError("unexpected semantic judge temperature")
    messages = payload["messages"]
    if not isinstance(messages, list) or len(messages) != 2:
        raise RuntimeError("missing semantic judge messages")
    for message, role in zip(messages, ("system", "user"), strict=True):
        if (
            not isinstance(message, dict)
            or set(message) != {"role", "content"}
            or message["role"] != role
            or not isinstance(message["content"], str)
            or not message["content"].strip()
        ):
            raise RuntimeError("invalid semantic judge message")
    try:
        supplied = json.loads(messages[1]["content"])
    except ValueError as exc:
        raise RuntimeError("invalid semantic judge evidence JSON") from exc
    required = {"task_context", "full_native_trace", "final_result"}
    if not isinstance(supplied, dict) or set(supplied) not in (required, required | {"native_trace_format"}):
        raise RuntimeError("unexpected semantic judge evidence fields")
    context = supplied["task_context"]
    if (
        not isinstance(context, dict)
        or set(context) != {"task_request", "shared_environment_context"}
        or not isinstance(context["task_request"], str)
        or not context["task_request"].strip()
        or not isinstance(context["shared_environment_context"], str)
    ):
        raise RuntimeError("missing actual task/shared context in semantic judgment")
    trace_format = supplied.get("native_trace_format", "claude-code")
    if trace_format not in ("claude-code", "codex"):
        raise RuntimeError("unsupported native trace format")
    raw = supplied["full_native_trace"]
    if not isinstance(raw, str) or not raw.strip() or raw != read_trace(trace_format + ".txt"):
        raise RuntimeError("semantic judgment did not receive the full archived native trace")
    rows = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    final = supplied["final_result"]
    if not isinstance(final, dict):
        raise RuntimeError("missing actual native final result")
    if trace_format == "claude-code":
        results = [row for row in rows if row.get("type") == "result"]
        if (
            not results
            or final != results[-1]
            or final.get("is_error")
            or not isinstance(final.get("result"), str)
            or not final["result"].strip()
        ):
            raise RuntimeError("semantic judgment final result disagrees with native trace")
    else:
        results = [
            row
            for row in rows
            if row.get("type") == "item.completed"
            and isinstance(row.get("item"), dict)
            and row["item"].get("type") == "agent_message"
            and isinstance(row["item"].get("text"), str)
            and row["item"]["text"].strip()
        ]
        if (
            not rows
            or rows[-1].get("type") != "turn.completed"
            or any(row.get("type") in ("turn.failed", "error") for row in rows)
            or not results
            or final != results[-1]
            or not isinstance(final["item"].get("text"), str)
            or not final["item"]["text"].strip()
        ):
            raise RuntimeError("semantic judgment final result disagrees with native Codex trace")


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
    breakdowns = [v for k, v in objects.items() if k.endswith("/evaluation-breakdown.json")]
    if len(breakdowns) > 1:
        raise RuntimeError("ambiguous Harbor scoring breakdown")
    breakdown = json.loads(breakdowns[0]) if breakdowns else None
    expected_reward = validate_assessments(behavior, judge, breakdown)
    if scoring_version(behavior) == 2:
        validate_judge_input(one("llm-judge-input.json"), lambda suffix: one(suffix, False))
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
    _same_score(reward, expected_reward, "native reward")
    if require_cleanup:
        cleanup = one("cleanup.json")
        if cleanup != {"uid": uid, "variant": variant, "deleted": True, "pods": pods}:
            raise RuntimeError("missing exact producer deletion confirmation")
    return inventory
