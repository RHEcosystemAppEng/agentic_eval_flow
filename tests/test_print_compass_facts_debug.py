"""Tests for the read-only Compass Facts diagnostic command."""

from __future__ import annotations

import json

from abevalflow.cli.compat.print_compass_facts_debug import main
from abevalflow.reporting.compass import print_compass_facts_debug


def test_reports_missing_scorecard(tmp_path, capsys):
    print_compass_facts_debug(tmp_path)
    assert "no scorecard.json" in capsys.readouterr().out


def test_reports_configured_push_with_no_attempts(tmp_path, capsys):
    (tmp_path / "scorecard.json").write_text(json.dumps({"policy": {"push_facts": {"entity_ref": "skill/demo"}}}))

    print_compass_facts_debug(tmp_path)

    assert "no facts were actually attempted" in capsys.readouterr().out


def test_prints_gate_payload_unmatched_fact_and_certification_result(tmp_path, capsys):
    scorecard = {
        "policy": {
            "push_facts": {
                "entity_ref": "skill/demo",
                "fact_ref_prefix": "catalog:test/",
            }
        },
        "gates": [
            {
                "gate_name": "security",
                "policy_key": "scanner",
                "passed": True,
                "score": 1.0,
                "mode": "blocking",
                "message": "clean",
                "details": {"findings": 0},
                "evaluated_at": "2026-01-01T00:00:00Z",
            }
        ],
        "fact_push_results": [
            {"fact_ref": "catalog:test/security_scanner", "success": True},
            {"fact_ref": "catalog:test/unmatched", "success": False, "error": "unavailable"},
        ],
        "certification_fact_push_results": [{"level": "trusted", "success": False, "error": "denied"}],
    }
    (tmp_path / "scorecard.json").write_text(json.dumps(scorecard))

    print_compass_facts_debug(tmp_path)

    out = capsys.readouterr().out
    assert '"factRef": "catalog:test/security_scanner"' in out
    assert '"entityRef": "skill/demo"' in out
    assert '"passed": true' in out
    assert "unmatched, raw record only" in out
    assert "certification fact [trusted] [FAILED (denied)]" in out


def test_cli_contains_malformed_scorecard_failure(tmp_path, capsys):
    (tmp_path / "scorecard.json").write_text("not JSON")

    assert main(["--report-dir", str(tmp_path)]) == 0
    assert "could not reconstruct Compass Facts" in capsys.readouterr().out
