"""End-to-end workflow evidence, dependencies, isolated staging, and retries."""

import json
import sys
from unittest.mock import patch

import pytest

from abevalflow.cli.main import main
from abevalflow.config.runtime import resolve_config
from abevalflow.workflow.service import load_run, run


def scanner(tmp_path, name, *, findings=None, exit_code=0, malformed=False):
    path = tmp_path / name
    path.write_text(f"""#!{sys.executable}
import json, sys
from pathlib import Path
for key in ('--output-json', '--output-quality', '--output-security'):
    if key in sys.argv:
        output = Path(sys.argv[sys.argv.index(key) + 1])
        output.write_text({repr("bad-json" if malformed else json.dumps({"findings": findings or []}))})
sys.exit({exit_code})
""")
    path.chmod(0o755)
    return str(path)


def configure(catalog, tmp_path, **options):
    return resolve_config(
        {
            **catalog,
            "security_command": scanner(tmp_path, "security-scanner"),
            "quality_command": scanner(tmp_path, "quality-scanner"),
            **options,
        },
        {},
    )


def test_full_static_workflow_produces_local_scorecard(catalog, tmp_path):
    config = configure(catalog, tmp_path)
    result = run(config)
    assert result.exit_code == 0, result.model_dump_json()
    assert [s.name for s in result.steps] == ["prepare", "structure", "security", "quality", "report"]
    assert all(s.status == "completed" for s in result.steps)
    assert (config.output / "reports/scorecard.json").is_file()
    assert (config.output / "reports/certification.json").is_file()
    assert (config.output / "reports/scorecard.md").is_file()
    assert (config.output / "source/pack/skills/demo/SKILL.md").read_bytes() == (
        catalog["skill"] / "SKILL.md"
    ).read_bytes()
    with pytest.raises(FileExistsError):
        run(config)


def test_failure_keeps_evidence_and_skips_live_evaluation(catalog, tmp_path):
    config = configure(
        catalog,
        tmp_path,
        tiers="1,2",
        security_command=scanner(
            tmp_path, "bad-security", findings=[{"severity": "HIGH", "message": "unsafe"}], exit_code=1
        ),
    )
    result = run(config)
    assert result.exit_code == 1
    assert next(s for s in result.steps if s.name == "security").verdict == "fail"
    assert next(s for s in result.steps if s.name == "evaluate").status == "skipped"
    assert next(s for s in result.steps if s.name == "report").status == "completed"


def test_missing_scanner_is_runtime_error_and_reports_are_written(catalog, tmp_path):
    config = configure(catalog, tmp_path, security_command="/does/not/exist")
    result = run(config)
    assert result.exit_code == 3
    assert next(s for s in result.steps if s.name == "quality").status == "completed"
    assert (config.output / "reports/scorecard.json").is_file()


def test_malformed_scanner_output_is_not_a_pass(catalog, tmp_path):
    config = configure(catalog, tmp_path, quality_command=scanner(tmp_path, "malformed", malformed=True))
    result = run(config)
    assert result.exit_code == 3
    assert next(s for s in result.steps if s.name == "quality").status == "error"


def test_run_manifest_restores_credentials_from_current_environment(catalog, tmp_path, monkeypatch):
    config = configure(catalog, tmp_path)
    run(config)
    monkeypatch.setenv("ABEVAL_DATABASE_URL", "sqlite:///restored.db")
    restored, result = load_run(config.output)
    assert restored.database_url.get_secret_value() == "sqlite:///restored.db"
    assert result.config["tiers"] == [1]


def test_external_symlink_is_rejected_without_modifying_source(catalog, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    (catalog["skill"] / "external").symlink_to(outside)
    result = run(configure(catalog, tmp_path))
    assert result.exit_code == 3
    assert result.steps[0].status == "error"
    assert outside.read_text() == "outside"


def test_internal_symlink_is_materialized(catalog, tmp_path):
    (catalog["source"] / "shared.md").write_text("shared")
    (catalog["skill"] / "references").mkdir()
    (catalog["skill"] / "references/shared.md").symlink_to("../../../../shared.md")
    config = configure(catalog, tmp_path)
    result = run(config)
    assert result.steps[0].status == "completed"
    staged = config.output / "source/pack/skills/demo/references/shared.md"
    assert staged.read_text() == "shared"
    assert not staged.is_symlink()


def test_phases_use_same_services_and_reject_duplicate_execution(catalog, tmp_path, capsys):
    config = configure(catalog, tmp_path)
    arguments = [str(config.skill), "--source", str(config.source), "--output", str(config.output)]
    assert main(["init", *arguments]) == 0
    assert main(["phase", "prepare", "--run-dir", str(config.output)]) == 0
    assert main(["phase", "structure", "--run-dir", str(config.output)]) == 0
    assert main(["phase", "structure", "--run-dir", str(config.output)]) == 2
    assert "Unable to execute" in capsys.readouterr().err


def test_pg_persistence_retry_is_idempotent_and_has_gate_records(catalog, tmp_path):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from abevalflow.db.models import GateResultRow, ScorecardRow
    from abevalflow.persistence.postgres import store_snapshot

    database = f"sqlite:///{tmp_path / 'results.db'}"
    config = configure(catalog, tmp_path, database_url=database, persist="postgres")
    result = run(config)
    assert result.exit_code == 0, result.model_dump_json()
    store_snapshot(config.output / "reports", database, config.run_id)
    with Session(create_engine(database)) as session:
        assert len(session.scalars(select(ScorecardRow)).all()) == 1
        assert len(session.scalars(select(GateResultRow)).all()) == 3


def test_persistence_destinations_attempted_independently(catalog, tmp_path):
    config = configure(
        catalog,
        tmp_path,
        persist="postgres,s3",
        database_url="sqlite://",
        s3_endpoint="https://s3.example",
        s3_access_key="key",
        s3_secret_key="secret",
    )
    with (
        patch("abevalflow.persistence.postgres.store_snapshot", side_effect=RuntimeError("secret-password")),
        patch("abevalflow.persistence.service.publish_s3", return_value={"uri": "s3://bucket/run"}) as s3,
    ):
        result = run(config)
    assert s3.called
    assert result.exit_code == 3
    assert [s.status for s in result.publication] == ["error", "completed"]
    assert "secret-password" not in result.model_dump_json()


def test_advisory_persistence_failure_does_not_change_gate_verdict(catalog, tmp_path):
    config = configure(catalog, tmp_path, persist="s3", persistence_required=False)
    result = run(config)
    assert result.exit_code == 0
    assert result.publication[0].status == "error"


def test_local_cli_returns_policy_failure(catalog, tmp_path, capsys):
    (catalog["skill"] / "SKILL.md").write_text("# Missing metadata")
    assert main(["validate", "skill", str(catalog["skill"])]) == 1
    assert "passed" in capsys.readouterr().out


def test_tier2_submission_validation_uses_metadata_mode(catalog, tmp_path):
    config = configure(catalog, tmp_path, tiers="2", submission_review="off")
    result = run(config)
    validation = next(s for s in result.steps if s.name == "submission")
    assert validation.verdict == "fail"
    assert "eval-control.yaml" in validation.reason
    assert next(s for s in result.steps if s.name == "evaluate").status == "skipped"


def test_report_computation_does_not_contact_compass(catalog, tmp_path):
    config = configure(catalog, tmp_path)
    with patch("abevalflow.compass_facts.urllib.request.urlopen") as request:
        run(config)
    request.assert_not_called()


def test_quality_findings_exit_is_policy_failure(catalog, tmp_path):
    config = configure(
        catalog,
        tmp_path,
        quality_command=scanner(
            tmp_path, "quality-finding", findings=[{"severity": "high", "message": "unsafe guidance"}], exit_code=1
        ),
    )
    result = run(config)
    quality = next(s for s in result.steps if s.name == "quality")
    assert quality.status == "completed"
    assert quality.verdict == "fail"
    assert result.exit_code == 1


def test_scanner_crash_with_partial_findings_remains_runtime_error(catalog, tmp_path):
    config = configure(
        catalog,
        tmp_path,
        security_command=scanner(
            tmp_path, "partial-security", findings=[{"severity": "high", "message": "partial"}], exit_code=2
        ),
    )
    result = run(config)
    assert next(s for s in result.steps if s.name == "security").status == "error"
    assert result.exit_code == 3


def test_structure_evidence_is_not_relabelled_as_quality_review(catalog, tmp_path):
    config = configure(catalog, tmp_path)
    run(config)
    scorecard = json.loads((config.output / "reports/scorecard.json").read_text())
    gate = next(g for g in scorecard["gates"] if g["policy_key"] == "structure")
    assert gate["gate_type"] == "validation"
