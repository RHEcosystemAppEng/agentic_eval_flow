"""Check the OpenShell NetworkPolicy's intended Tekton pod identity."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
EVALUATE_SELECTOR = {"tekton.dev/pipeline": "abevalflow-pipeline-openshell", "tekton.dev/task": "evaluate"}


def _matches(selector: dict[str, str], labels: dict[str, str]) -> bool:
    return all(labels.get(key) == value for key, value in selector.items())


def test_gateway_and_egress_select_only_evaluate_task_pods():
    pipeline = yaml.safe_load((REPO / "pipeline/pipelines/ci-pipeline-openshell.yaml").read_text())
    evaluate = next(task for task in pipeline["spec"]["tasks"] if task["name"] == "evaluate")
    assert pipeline["metadata"]["name"] == EVALUATE_SELECTOR["tekton.dev/pipeline"]
    assert evaluate["taskRef"]["name"] == EVALUATE_SELECTOR["tekton.dev/task"]

    policies = list(yaml.safe_load_all((REPO / "config/forge-saw/networkpolicy-ci-openshell.yaml").read_text()))
    assert len(policies) == 3
    ingress_selectors = [
        rule["podSelector"]["matchLabels"]
        for policy in policies[:2]
        for rule in policy["spec"]["ingress"][0]["from"]
    ]
    egress_selector = policies[2]["spec"]["podSelector"]["matchLabels"]
    remote_gateway = yaml.safe_load((REPO / "config/forge-saw/networkpolicy-gateway-from-abeval.yaml").read_text())
    remote_selector = remote_gateway["spec"]["ingress"][0]["from"][0]["podSelector"]["matchLabels"]
    assert ingress_selectors + [egress_selector, remote_selector] == [EVALUATE_SELECTOR] * 4

    evaluate_pod = {**EVALUATE_SELECTOR, "app.kubernetes.io/part-of": "abevalflow"}
    prepare_pod = {**evaluate_pod, "tekton.dev/task": "prepare"}
    unrelated_pod = {"app.kubernetes.io/part-of": "abevalflow"}
    other_pipeline = {**evaluate_pod, "tekton.dev/pipeline": "other-pipeline"}
    for selector in ingress_selectors + [egress_selector, remote_selector]:
        assert _matches(selector, evaluate_pod)
        assert not _matches(selector, prepare_pod)
        assert not _matches(selector, unrelated_pod)
        assert not _matches(selector, other_pipeline)
