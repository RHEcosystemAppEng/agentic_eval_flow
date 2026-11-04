"""Sequential workflows preserve staging, reports, publication, and item identity."""

import json
import shutil
import tarfile
from unittest.mock import patch

import pytest

from abevalflow.cli.main import main
from abevalflow.config.runtime import ConfigurationError
from abevalflow.results.konflux import emit_results, package_batch, summarize
from abevalflow.results.run import StepResult
from abevalflow.workflow.batch import run_batch
from abevalflow.workflow.operations import Operations
from tests.test_workflow import scanner


def batch_options(catalog, tmp_path, **overrides):
    return dict(
        source=catalog["source"],
        output=tmp_path / "batch",
        run_id="batch-test",
        revision="abc123",
        security_command=scanner(tmp_path, "security"),
        quality_command=scanner(tmp_path, "quality"),
        **overrides,
    )


def test_sequential_batch_preserves_full_source_and_publishes_once_per_skill(catalog, tmp_path):
    second = catalog["skill"].parent / "other"
    shutil.copytree(catalog["skill"], second)
    (second / "SKILL.md").write_text((second / "SKILL.md").read_text().replace("demo", "other"))
    events = []
    original = Operations.execute

    def execute(self, name, result):
        events.append((self.config.name, name))
        return original(self, name, result)

    with (
        patch.object(Operations, "execute", execute),
        patch("abevalflow.persistence.service.publish", return_value=[StepResult(name="s3")]) as publish,
    ):
        directory, result = run_batch(["pack/demo", "pack/other"], batch_options(catalog, tmp_path, persist="s3"), {})
    assert result.exit_code == 0
    assert publish.call_count == 2
    assert [name for skill, name in events if skill == "demo"] == [
        "prepare",
        "structure",
        "security",
        "quality",
        "report",
    ]
    assert [skill for skill, _ in events] == ["demo"] * 5 + ["other"] * 5
    assert len({item.run_id for item in result.items}) == 2
    for item in result.items:
        run = directory / item.run_dir
        assert (run / "reports/scorecard.json").is_file()
        assert (run / "source/pack/skills/demo/SKILL.md").is_file()
        assert (run / "source/pack/skills/other/SKILL.md").is_file()
    summary = summarize(directory, ["pack/demo", "pack/other"], "abc123", {1}, {"s3"})
    assert summary["results"]["tier1"]["result"] == "SUCCESS"
    assert summary["items"][1]["tier2"]["result"] == "SKIPPED"
    assert summary["results"]["tier2"]["result"] == "SKIPPED"
    package_batch(directory)
    with tarfile.open(directory / "reports.tar.gz") as archive:
        names = archive.getnames()
        assert "batch.json" in names and "runs/0000/reports/scorecard.json" in names
        assert not any("/source/" in name for name in names)


def test_skipped_tier_is_preserved_in_tekton_results(catalog, tmp_path):
    directory, _ = run_batch(["pack/demo"], batch_options(catalog, tmp_path), {})
    results = tmp_path / "tekton-results"
    assert emit_results(directory, ["pack/demo"], "abc123", {1}, set(), results) == 0
    assert json.loads((results / "TEST_OUTPUT_TIER_B").read_text())["result"] == "SKIPPED"
    assert json.loads((results / "TEST_OUTPUT").read_text())["result"] == "SUCCESS"


def test_missing_skill_is_recorded_and_later_skill_still_runs(catalog, tmp_path):
    directory, result = run_batch(["pack/missing", "pack/demo"], batch_options(catalog, tmp_path), {})
    assert result.exit_code == 3
    assert [i.status for i in result.items] == ["error", "completed"]
    assert emit_results(directory, ["pack/missing", "pack/demo"], "abc123", {1}, set(), tmp_path / "results") == 1
    summary = json.loads((directory / "summary.json").read_text())
    assert summary["items"][0]["tier1"]["result"] == "ERROR"
    assert summary["items"][1]["tier1"]["result"] == "SUCCESS"


def test_cleanup_failure_stops_batch_but_preserves_finalization(catalog, tmp_path):
    from abevalflow.execution.cleanup import CleanupError
    from tests.test_tier2_workflow import prepare_submission

    prepare_submission(catalog)
    with patch.object(Operations, "evaluate", side_effect=CleanupError("unsafe")):
        directory, result = run_batch(
            ["pack/demo", "pack/not-started"], batch_options(catalog, tmp_path, tiers="2"), {}
        )
    assert result.exit_code == 3
    assert result.items[1].status == "error"
    assert "cleanup" in result.items[1].reason
    child = json.loads((directory / result.items[0].run_dir / "run.json").read_text())
    assert child["steps"][-1]["name"] == "report"


@pytest.mark.parametrize("skills", [["pack/demo", "pack/demo"], ["../escape"], ["pack/../escape"]])
def test_invalid_identifiers_are_rejected_before_creating_evidence(catalog, tmp_path, skills):
    with pytest.raises(ConfigurationError):
        run_batch(skills, batch_options(catalog, tmp_path), {})
    assert not (tmp_path / "batch").exists()


def test_batch_source_cannot_recursively_copy_batch_output(catalog, tmp_path):
    options = batch_options(catalog, tmp_path)
    options["output"] = catalog["source"] / "results"
    with pytest.raises(ConfigurationError, match="outside"):
        run_batch(["pack/demo"], options, {})


def test_empty_cli_batch_and_result_export_are_successful(tmp_path):
    directory = tmp_path / "batch"
    assert main(["batch", "--output", str(directory), "--revision", "abc", "--skill-ids"]) == 0
    assert main(["batch-package", "--batch-dir", str(directory)]) == 0
    assert (
        main(
            [
                "batch-results",
                "--batch-dir",
                str(directory),
                "--revision",
                "abc",
                "--tiers",
                "1",
                "--results-dir",
                str(tmp_path / "results"),
                "--skill-ids",
            ]
        )
        == 0
    )


@pytest.mark.parametrize("tamper", ["revision", "skills", "pending", "run_revision", "run_identity", "tiers"])
def test_export_fails_closed_on_mismatched_or_incomplete_evidence(catalog, tmp_path, tamper):
    directory, result = run_batch(["pack/demo"], batch_options(catalog, tmp_path), {})
    batch = json.loads((directory / "batch.json").read_text())
    if tamper == "revision":
        batch["source_revision"] = "wrong"
    elif tamper == "skills":
        batch["items"][0]["skill_id"] = "pack/wrong"
    elif tamper == "pending":
        batch["completed_at"] = None
    else:
        path = directory / result.items[0].run_dir / "run.json"
        child = json.loads(path.read_text())
        if tamper == "run_revision":
            child["source_revision"] = "wrong"
        elif tamper == "run_identity":
            child["run_id"] = "wrong"
        else:
            child["config"]["tiers"] = [2]
        path.write_text(json.dumps(child))
    (directory / "batch.json").write_text(json.dumps(batch))
    assert emit_results(directory, ["pack/demo"], "abc123", {1}, set(), tmp_path / "results") == 1


def test_missing_manifest_and_failed_oci_attachment_emit_error_results(tmp_path):
    directory = tmp_path / "missing"
    assert (
        emit_results(
            directory,
            ["pack/demo"],
            "abc123",
            {1},
            set(),
            tmp_path / "results",
            "quay.io/example/image",
            "sha256:" + "a" * 64,
        )
        == 1
    )
    assert json.loads((tmp_path / "results/TEST_OUTPUT_PUBLICATION").read_text())["result"] == "ERROR"


def test_completed_trial_failure_is_informational_and_not_retried(catalog, tmp_path):
    from tests.test_tier2_workflow import prepare_submission

    prepare_submission(catalog)
    with (
        patch.object(
            Operations, "evaluate", return_value=StepResult(name="evaluate", tier=2, verdict="fail")
        ) as evaluate,
        patch.object(Operations, "report", return_value=StepResult(name="report")),
    ):
        directory, result = run_batch(["pack/demo"], batch_options(catalog, tmp_path, tiers="2"), {})
    evaluate.assert_called_once()
    assert result.exit_code == 1
    assert emit_results(directory, ["pack/demo"], "abc123", {2}, set(), tmp_path / "results") == 0
    assert json.loads((tmp_path / "results/TEST_OUTPUT_TIER_B").read_text())["result"] == "FAILURE"


def test_timeout_retry_does_not_repeat_scans_reviews_reporting_or_publication(catalog, tmp_path):
    import subprocess
    from unittest.mock import MagicMock

    from tests.test_tier2_workflow import prepare_submission, write_evaluation

    prepare_submission(catalog, mode="single")
    runtime = MagicMock()
    invocations = []

    def native(arguments, **kwargs):
        invocations.append(arguments)
        if len(invocations) == 1:
            raise subprocess.TimeoutExpired("native", 1)
        write_evaluation(Path(arguments[arguments.index("--output") + 1]))
        return 0

    from pathlib import Path

    runtime.run.side_effect = native
    phases = []
    original = Operations.execute

    def execute(self, name, result):
        phases.append(name)
        return original(self, name, result)

    with (
        patch("abevalflow.execution.base.resolve_runtime", return_value=runtime),
        patch("abevalflow.execution.harbor.cleanup_trials"),
        patch.object(Operations, "execute", execute),
        patch("abevalflow.persistence.service.publish", return_value=[StepResult(name="s3")]) as publication,
    ):
        directory, result = run_batch(["pack/demo"], batch_options(catalog, tmp_path, tiers="1,2", persist="s3"), {})
    assert result.exit_code == 0
    assert phases == ["prepare", "structure", "security", "quality", "submission", "evaluate", "report"]
    assert len(invocations) == 2
    publication.assert_called_once()
    summary = summarize(directory, ["pack/demo"], "abc123", {1, 2}, {"s3"})
    assert all(v["result"] == "SUCCESS" for v in summary["results"].values())
