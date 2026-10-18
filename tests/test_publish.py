"""Tests for scripts/publish.py — artifact publishing and cleanup."""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.publish import (
    _build_artifact_prefix,
    upload_aeh_debug_artifacts,
    upload_aeh_run_artifacts,
    upload_reports,
)


@pytest.fixture
def report_dir(tmp_path: Path) -> Path:
    """Create a temporary report directory with sample files."""
    report = {
        "submission_name": "hello-world",
        "provenance": {
            "commit_sha": "abc123def456",
            "pipeline_run_id": "abevalflow-xyz",
        },
        "summary": {
            "treatment": {
                "n_trials": 5,
                "n_passed": 4,
                "n_failed": 1,
                "n_errors": 0,
                "pass_rate": 0.8,
                "mean_reward": 0.75,
            },
            "control": {
                "n_trials": 5,
                "n_passed": 2,
                "n_failed": 3,
                "n_errors": 0,
                "pass_rate": 0.4,
                "mean_reward": 0.35,
            },
            "uplift": 0.4,
            "recommendation": "pass",
        },
        "trials": {},
    }
    (tmp_path / "report.json").write_text(json.dumps(report))
    (tmp_path / "report.md").write_text("# Report\nSample markdown")
    return tmp_path


class TestBuildArtifactPrefix:
    def test_format(self):
        prefix = _build_artifact_prefix("hello-world", "abevalflow-xyz")
        parts = prefix.split("_")
        assert len(parts) >= 4
        assert parts[0].isdigit() and len(parts[0]) == 8  # YYYYMMDD
        assert parts[1].isdigit() and len(parts[1]) == 6  # hhmmss
        assert "hello-world" in prefix
        assert "abevalflow-xyz" in prefix

    def test_datestamp_is_today(self):
        from datetime import datetime

        today = datetime.now(UTC).strftime("%Y%m%d")
        prefix = _build_artifact_prefix("test", "run1")
        assert prefix.startswith(today)


class TestUploadReports:
    @patch("minio.Minio")
    def test_uploads_both_files(self, mock_minio_cls, report_dir):
        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        prefix = upload_reports(
            report_dir=report_dir,
            submission_name="hello-world",
            pipeline_run_id="run-123",
            endpoint="http://minio:9000",
            access_key="key",
            secret_key="secret",
        )

        assert prefix is not None
        assert "hello-world" in prefix
        assert "run-123" in prefix
        assert mock_client.fput_object.call_count == 2

    @patch("minio.Minio")
    def test_creates_bucket_if_missing(self, mock_minio_cls, report_dir):
        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.bucket_exists.return_value = False

        upload_reports(
            report_dir=report_dir,
            submission_name="test",
            pipeline_run_id="run-1",
            endpoint="http://minio:9000",
            access_key="key",
            secret_key="secret",
        )

        mock_client.make_bucket.assert_called_once_with("ab-eval-reports")

    @patch("minio.Minio")
    def test_returns_none_on_empty_dir(self, mock_minio_cls, tmp_path):
        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        result = upload_reports(
            report_dir=tmp_path,
            submission_name="test",
            pipeline_run_id="run-1",
            endpoint="http://minio:9000",
            access_key="key",
            secret_key="secret",
        )

        assert result is None

    @patch("minio.Minio")
    def test_uploads_security_scan_files_even_with_report_prefix(self, mock_minio_cls, report_dir):
        """Regression test: Tier A findings must upload even when a caller
        (e.g. aeh-skill-eval.yaml's store step) always passes report_prefix
        -- previously a stale guard skipped them whenever report_prefix was
        set, under the assumption a now-nonexistent separate task had
        already uploaded them."""
        (report_dir / "security-scan.json").write_text('{"findings": []}')
        (report_dir / "security-scan.sarif").write_text("{}")
        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        prefix = upload_reports(
            report_dir=report_dir,
            submission_name="hello-world",
            pipeline_run_id="run-123",
            endpoint="http://minio:9000",
            access_key="key",
            secret_key="secret",
            report_prefix="20260101_000000_hello-world_run-123",
        )

        assert prefix == "20260101_000000_hello-world_run-123"
        object_names = [c.args[1] for c in mock_client.fput_object.call_args_list]
        assert f"{prefix}/security_scans/security-scan.json" in object_names
        assert f"{prefix}/security_scans/security-scan.sarif" in object_names

    @patch("minio.Minio")
    def test_uploads_skillmd_scan_files(self, mock_minio_cls, report_dir):
        """harness-eval's SKILL.md security findings live in report_dir;
        its quality findings live at the leg's workspace root instead."""
        (report_dir / "skillmd-security-scan.json").write_text('{"findings": []}')
        workspace_root = report_dir.parent
        (workspace_root / "skillmd-quality-scan.json").write_text('{"findings": []}')
        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        prefix = upload_reports(
            report_dir=report_dir,
            submission_name="hello-world",
            pipeline_run_id="run-123",
            endpoint="http://minio:9000",
            access_key="key",
            secret_key="secret",
            report_prefix="20260101_000000_hello-world_run-123",
            workspace_root=workspace_root,
        )

        object_names = [c.args[1] for c in mock_client.fput_object.call_args_list]
        assert f"{prefix}/security_scans/skillmd-security-scan.json" in object_names
        assert f"{prefix}/security_scans/skillmd-quality-scan.json" in object_names

    @patch("minio.Minio")
    def test_skillmd_quality_scan_skipped_without_workspace_root(self, mock_minio_cls, report_dir):
        """Without workspace_root, skillmd-quality-scan.json (leg-root, not
        report_dir-scoped) simply isn't found -- no crash, just not found."""
        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        upload_reports(
            report_dir=report_dir,
            submission_name="hello-world",
            pipeline_run_id="run-123",
            endpoint="http://minio:9000",
            access_key="key",
            secret_key="secret",
        )

        object_names = [c.args[1] for c in mock_client.fput_object.call_args_list]
        assert not any("skillmd-quality-scan.json" in n for n in object_names)

    @patch("minio.Minio")
    def test_security_scan_only_still_returns_prefix(self, mock_minio_cls, tmp_path):
        """A Tier A-only leg (no report.json/report.md -- Tier B never ran)
        must still produce a usable prefix as long as a scan file exists,
        so its findings are reachable in MinIO."""
        (tmp_path / "security-scan.json").write_text('{"findings": []}')
        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        prefix = upload_reports(
            report_dir=tmp_path,
            submission_name="test",
            pipeline_run_id="run-1",
            endpoint="http://minio:9000",
            access_key="key",
            secret_key="secret",
        )

        assert prefix is not None


@pytest.fixture
def results_dir(tmp_path: Path) -> Path:
    """Create a mock Harbor results directory with agent/verifier folders."""
    for variant in ("treatment", "control"):
        trial = tmp_path / variant / "job-name" / "trial_001__uuid1"
        (trial / "agent").mkdir(parents=True)
        (trial / "agent" / "agent.log").write_text("agent log content")
        (trial / "agent" / "solution.py").write_text("print('hello')")
        (trial / "verifier").mkdir(parents=True)
        (trial / "verifier" / "verifier.log").write_text("verifier log")
        (trial / "result.json").write_text('{"status": "pass"}')
    return tmp_path


class TestUploadAehDebugArtifacts:
    @pytest.fixture
    def aeh_jobs_dir(self, tmp_path: Path) -> Path:
        job = tmp_path / "2026-07-19__08-44-52"
        trial = job / "case-001__abc"
        (trial / "agent").mkdir(parents=True)
        (trial / "verifier").mkdir(parents=True)
        (trial / "agent" / "claude-code.txt").write_text("{}")
        (trial / "verifier" / "reward.json").write_text('{"reward": 1.0}')
        (trial / "exception.txt").write_text("boom")
        (job / "result.json").write_text("{}")
        (job / "job.log").write_text("ok")
        return tmp_path

    @patch("minio.Minio")
    def test_uploads_unpacked_job_tree(self, mock_minio_cls, aeh_jobs_dir):
        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        count = upload_aeh_debug_artifacts(
            results_dir=aeh_jobs_dir,
            prefix="20260719_prep_aeh-hello_run1",
            endpoint="http://minio:9000",
            access_key="key",
            secret_key="secret",
        )

        assert count == 5
        names = [call[0][1] for call in mock_client.fput_object.call_args_list]
        assert all(n.startswith("20260719_prep_aeh-hello_run1/debug/harbor/") for n in names)
        assert any("2026-07-19__08-44-52/case-001__abc/agent/claude-code.txt" in n for n in names)
        assert any("2026-07-19__08-44-52/result.json" in n for n in names)
        assert not any(n.endswith(".tar.gz") for n in names)

    def test_missing_jobs_dir(self, tmp_path):
        assert (
            upload_aeh_debug_artifacts(
                results_dir=tmp_path / "missing",
                prefix="p",
                endpoint="http://minio:9000",
                access_key="k",
                secret_key="s",
            )
            == 0
        )

    @patch("minio.Minio")
    def test_uploads_pairwise_control_and_treatment_jobs(self, mock_minio_cls, tmp_path: Path):
        """Pairwise jobs live under aeh-*-jobs-* siblings of _eval_tmp."""
        eval_tmp = tmp_path / "_eval_tmp"
        for label in ("control", "treatment"):
            job = eval_tmp / f"aeh-{label}-jobs-run1" / "2026-07-19__12-00-00"
            trial = job / "case-001__abc"
            (trial / "agent").mkdir(parents=True)
            (trial / "agent" / "out.txt").write_text(label)
            (job / "result.json").write_text("{}")

        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        count = upload_aeh_debug_artifacts(
            results_dir=eval_tmp,
            prefix="prefix1",
            endpoint="http://minio:9000",
            access_key="key",
            secret_key="secret",
        )

        assert count == 4
        names = [call[0][1] for call in mock_client.fput_object.call_args_list]
        assert any("debug/harbor/control/2026-07-19__12-00-00/" in n for n in names)
        assert any("debug/harbor/treatment/2026-07-19__12-00-00/" in n for n in names)
        assert not any("aeh-control-jobs" in n or "aeh-treatment-jobs" in n for n in names)

    @patch("minio.Minio")
    def test_single_flattens_aeh_jobs_segment(self, mock_minio_cls, tmp_path: Path):
        """Single mode: _eval_tmp/aeh-jobs/<ts> → debug/harbor/<ts>/ (no aeh-jobs/)."""
        eval_tmp = tmp_path / "_eval_tmp"
        job = eval_tmp / "aeh-jobs" / "2026-07-19__12-00-00"
        job.mkdir(parents=True)
        (job / "result.json").write_text("{}")
        (job / "job.log").write_text("ok")

        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        count = upload_aeh_debug_artifacts(
            results_dir=eval_tmp,
            prefix="prefix1",
            endpoint="http://minio:9000",
            access_key="key",
            secret_key="secret",
        )
        assert count == 2
        names = [call[0][1] for call in mock_client.fput_object.call_args_list]
        assert any(n.endswith("debug/harbor/2026-07-19__12-00-00/result.json") for n in names)
        assert not any("/aeh-jobs/" in n for n in names)


class TestUploadAehRunArtifacts:
    def _make_run(self, root: Path, name: str, *, pairwise: bool = False) -> Path:
        run = root / name
        (run / "cases" / "case-001" / "artifacts").mkdir(parents=True)
        (run / "summary.yaml").write_text(
            "run_id: x\n" + ("pairwise:\n  wins_a: 0\n  ties: 1\n" if pairwise else "judges: {}\n")
        )
        (run / "run_result.json").write_text('{"mean_reward": 1.0}')
        (run / "report.html").write_text("<html>ok</html>")
        (run / "cases" / "case-001" / "artifacts" / "greeting.txt").write_text("Hello")
        return run

    @patch("minio.Minio")
    def test_uploads_single_run_tree(self, mock_minio_cls, tmp_path: Path):
        report_dir = tmp_path / "reports" / "aeh-hello-world-single"
        report_dir.mkdir(parents=True)
        self._make_run(report_dir, "aeh-single-run1")
        (report_dir / "report.json").write_text("{}")

        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        count = upload_aeh_run_artifacts(
            report_dir=report_dir,
            prefix="prefix-single",
            endpoint="http://minio:9000",
            access_key="k",
            secret_key="s",
        )
        assert count == 4
        names = [call[0][1] for call in mock_client.fput_object.call_args_list]
        assert any(n.endswith("debug/aeh/aeh-single-run1/summary.yaml") for n in names)
        assert any(n.endswith("debug/aeh/aeh-single-run1/report.html") for n in names)
        assert any(n.endswith("debug/aeh/aeh-single-run1/run_result.json") for n in names)
        assert any("debug/aeh/aeh-single-run1/cases/case-001/artifacts/greeting.txt" in n for n in names)
        # Top-level aggregate report.json must not be nested under debug/aeh/
        assert not any("/debug/aeh/report.json" in n for n in names)

    @patch("minio.Minio")
    def test_uploads_pairwise_control_and_treatment_runs(self, mock_minio_cls, tmp_path: Path):
        report_dir = tmp_path / "reports" / "aeh-hello-world-pairwise"
        report_dir.mkdir(parents=True)
        self._make_run(report_dir, "control-run1")
        self._make_run(report_dir, "treatment-run1", pairwise=True)

        mock_client = MagicMock()
        mock_minio_cls.return_value = mock_client
        mock_client.bucket_exists.return_value = True

        count = upload_aeh_run_artifacts(
            report_dir=report_dir,
            prefix="prefix-pw",
            endpoint="http://minio:9000",
            access_key="k",
            secret_key="s",
        )
        assert count == 8
        names = [call[0][1] for call in mock_client.fput_object.call_args_list]
        assert any("debug/aeh/control-run1/summary.yaml" in n for n in names)
        assert any("debug/aeh/treatment-run1/summary.yaml" in n for n in names)
        assert any("debug/aeh/treatment-run1/report.html" in n for n in names)

    def test_missing_runs_returns_zero(self, tmp_path: Path):
        report_dir = tmp_path / "reports" / "empty"
        report_dir.mkdir(parents=True)
        (report_dir / "report.json").write_text("{}")
        assert (
            upload_aeh_run_artifacts(
                report_dir=report_dir,
                prefix="p",
                endpoint="http://minio:9000",
                access_key="k",
                secret_key="s",
            )
            == 0
        )
