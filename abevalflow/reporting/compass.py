"""Reporting compass services."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def print_compass_facts_debug(report_dir: Path) -> None:
    scorecard_path = report_dir / "scorecard.json"
    if not scorecard_path.is_file():
        print("=== Compass Facts: no scorecard.json, nothing to show ===")
        return

    sc = json.loads(scorecard_path.read_text())
    push_facts_cfg = (sc.get("policy") or {}).get("push_facts")
    fact_results = sc.get("fact_push_results") or []
    cert_fact_results = sc.get("certification_fact_push_results") or []

    if not push_facts_cfg:
        print("=== Compass Facts: push_facts not configured for this submission ===")
        return

    print("=== Compass Facts submitted this run ===")
    entity_ref = push_facts_cfg.get("entity_ref")
    prefix = push_facts_cfg.get("fact_ref_prefix", "catalog:default/abevalflow_")

    by_ref = {f.get("fact_ref"): f for f in fact_results}
    shown: set[str] = set()
    for gate in sc.get("gates") or []:
        gate_name = gate.get("gate_name")
        policy_key = gate.get("policy_key") or gate_name
        expected_ref = f"{prefix}{gate_name}_{policy_key}" if policy_key != gate_name else f"{prefix}{gate_name}"
        match = by_ref.get(expected_ref)
        if match is None:
            continue
        shown.add(expected_ref)
        fact_json = {
            "factRef": expected_ref,
            "entityRef": entity_ref,
            "data": {
                "gate_name": gate_name,
                "passed": gate.get("passed"),
                "score": gate.get("score"),
                "mode": gate.get("mode"),
                "message": gate.get("message") or "",
                "details": gate.get("details") or {},
                "evaluated_at": gate.get("evaluated_at"),
            },
        }
        status = "SUCCESS" if match.get("success") else f"FAILED ({match.get('error')})"
        print(f"--- gate fact [{status}] ---")
        print(json.dumps(fact_json, indent=2))

    for f in fact_results:
        if f.get("fact_ref") not in shown:
            print("--- gate fact [unmatched, raw record only] ---")
            print(json.dumps(f, indent=2))

    for cert in cert_fact_results:
        level = cert.get("level", "certification")
        status = "SUCCESS" if cert.get("success") else f"FAILED ({cert.get('error')})"
        print(f"--- certification fact [{level}] [{status}] ---")
        print(json.dumps(cert, indent=2))

    if not fact_results and not cert_fact_results:
        print(
            "(push_facts was configured, but no facts were actually attempted -- "
            "check that at least one gate has push_fact: true)"
        )
