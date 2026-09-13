"""Tests for scripts/analyze.py — A/B evaluation analysis and reporting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abevalflow.report import (
    AnalysisResult,
    Provenance,
    Recommendation,
    ScanMode,
    SecurityFinding,
    SecurityScanResult,
    SecuritySeverity,
    TrialResult,
    VariantSummary,
)
from scripts.analyze import (
    _extract_reward,
    build_analysis,
    compute_fisher,
    compute_ttest,
    compute_variant_summary,
    main,
    parse_variant_trials,
    render_markdown,
)

# ---------------------------------------------------------------------------
# Fixtures — fake result directories
# ---------------------------------------------------------------------------


def _write_result(trial_dir: Path, reward: float | None, nested: bool = True) -> None:
    """Write a result.json file to a trial directory."""
    trial_dir.mkdir(parents=True, exist_ok=True)
    if reward is None:
        (trial_dir / "result.json").write_text("{}")
        return
    if nested:
        data = {"verifier_result": {"rewards": {"reward": reward}}}
    else:
        data = {"verifier_result": {"reward": reward}}
    (trial_dir / "result.json").write_text(json.dumps(data))


@pytest.fixture()
def results_dir(tmp_path: Path) -> Path:
    """Create a results directory with 5 treatment and 5 control trials."""
    base = tmp_path / "eval-results" / "my-submission"

    treatment_job = base / "treatment" / "my-submission-treatment"
    for i, reward in enumerate([1.0, 0.8, 0.6, 0.0, 1.0]):
        _write_result(treatment_job / f"task__{i:03d}", reward)

    control_job = base / "control" / "my-submission-control"
    for i, reward in enumerate([0.5, 0.0, 0.3, 0.0, 0.0]):
        _write_result(control_job / f"task__{i:03d}", reward)

    return base


@pytest.fixture()
def all_pass_dir(tmp_path: Path) -> Path:
    """All trials pass with high reward (slight variance to avoid scipy warnings)."""
    base = tmp_path / "all-pass"
    for variant in ("treatment", "control"):
        job = base / variant / f"sub-{variant}"
        for i in range(5):
            _write_result(job / f"t__{i:03d}", 0.95 + 0.01 * i)
    return base


@pytest.fixture()
def empty_results_dir(tmp_path: Path) -> Path:
    """Result directory with no trial subdirectories."""
    base = tmp_path / "empty"
    (base / "treatment").mkdir(parents=True)
    (base / "control").mkdir(parents=True)
    return base


# ---------------------------------------------------------------------------
# TestExtractReward
# ---------------------------------------------------------------------------


class TestExtractReward:
    def test_nested_format(self):
        data = {"verifier_result": {"rewards": {"reward": 0.75}}}
        assert _extract_reward(data) == 0.75

    def test_flat_format(self):
        data = {"verifier_result": {"reward": 0.5}}
        assert _extract_reward(data) == 0.5

    def test_nested_takes_precedence(self):
        data = {"verifier_result": {"rewards": {"reward": 0.9}, "reward": 0.1}}
        assert _extract_reward(data) == 0.9

    def test_missing_verifier_result(self):
        assert _extract_reward({}) is None

    def test_verifier_result_not_dict(self):
        assert _extract_reward({"verifier_result": "bad"}) is None

    def test_empty_rewards(self):
        data = {"verifier_result": {"rewards": {}}}
        assert _extract_reward(data) is None

    def test_zero_reward(self):
        data = {"verifier_result": {"reward": 0.0}}
        assert _extract_reward(data) == 0.0

    def test_integer_reward_cast(self):
        data = {"verifier_result": {"reward": 1}}
        assert _extract_reward(data) == 1.0
        assert isinstance(_extract_reward(data), float)


# ---------------------------------------------------------------------------
# TestParseVariantTrials
# ---------------------------------------------------------------------------


class TestParseVariantTrials:
    def test_parses_all_trials(self, results_dir: Path):
        trials = parse_variant_trials(results_dir / "treatment")
        assert len(trials) == 5

    def test_rewards_extracted(self, results_dir: Path):
        trials = parse_variant_trials(results_dir / "treatment")
        rewards = [t.reward for t in trials]
        assert all(r is not None for r in rewards)

    def test_pass_classification(self, results_dir: Path):
        trials = parse_variant_trials(results_dir / "treatment")
        passed_count = sum(1 for t in trials if t.passed)
        assert passed_count == 4

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path):
        trials = parse_variant_trials(tmp_path / "nope")
        assert trials == []

    def test_corrupt_json_counted_as_error(self, tmp_path: Path):
        variant = tmp_path / "variant" / "job"
        trial = variant / "bad_trial"
        trial.mkdir(parents=True)
        (trial / "result.json").write_text("not json{{{")
        trials = parse_variant_trials(tmp_path / "variant")
        assert len(trials) == 1
        assert trials[0].reward is None
        assert not trials[0].passed

    def test_flat_format_parsed(self, tmp_path: Path):
        variant = tmp_path / "variant" / "job"
        _write_result(variant / "trial_0", 0.85, nested=False)
        trials = parse_variant_trials(tmp_path / "variant")
        assert len(trials) == 1
        assert trials[0].reward == pytest.approx(0.85)

    def test_empty_result_json_skipped(self, tmp_path: Path):
        """result.json without verifier_result is treated as job-level and skipped."""
        variant = tmp_path / "variant" / "job"
        trial = variant / "trial_0"
        trial.mkdir(parents=True)
        (trial / "result.json").write_text("{}")
        trials = parse_variant_trials(tmp_path / "variant")
        assert len(trials) == 0

    def test_job_level_result_filtered(self, tmp_path: Path):
        """Job-level result.json (no verifier_result) must not inflate trial count."""
        variant = tmp_path / "variant"
        job_dir = variant / "sub-treatment"
        job_dir.mkdir(parents=True)
        (job_dir / "result.json").write_text(json.dumps({"job_name": "sub-treatment"}))
        trial_dir = job_dir / "task__001"
        trial_dir.mkdir()
        (trial_dir / "result.json").write_text(json.dumps({"verifier_result": {"rewards": {"reward": 1.0}}}))
        trials = parse_variant_trials(variant)
        assert len(trials) == 1
        assert trials[0].reward == 1.0


# ---------------------------------------------------------------------------
# TestComputeVariantSummary
# ---------------------------------------------------------------------------


class TestComputeVariantSummary:
    def test_basic_stats(self, results_dir: Path):
        trials = parse_variant_trials(results_dir / "treatment")
        summary = compute_variant_summary(trials)
        assert summary.n_trials == 5
        assert summary.n_passed == 4
        assert summary.n_failed == 1
        assert summary.pass_rate == pytest.approx(0.8)
        assert summary.mean_reward == pytest.approx(0.68)
        assert summary.std_reward is not None

    def test_empty_trials(self):
        summary = compute_variant_summary([])
        assert summary.n_trials == 0
        assert summary.pass_rate == 0.0
        assert summary.mean_reward is None
        assert summary.median_reward is None
        assert summary.std_reward is None

    def test_single_trial(self):
        trials = [TrialResult(trial_name="t1", reward=0.5)]
        summary = compute_variant_summary(trials)
        assert summary.n_trials == 1
        assert summary.mean_reward == 0.5
        assert summary.std_reward is None

    def test_error_trials_counted(self):
        trials = [
            TrialResult(trial_name="t1", reward=1.0),
            TrialResult(trial_name="t2"),
        ]
        summary = compute_variant_summary(trials)
        assert summary.n_errors == 1
        assert summary.n_passed == 1
        assert summary.n_trials == 2

    def test_all_zero_rewards(self):
        trials = [TrialResult(trial_name=f"t{i}", reward=0.0) for i in range(5)]
        summary = compute_variant_summary(trials)
        assert summary.pass_rate == 0.0
        assert summary.mean_reward == 0.0
        assert summary.n_passed == 0


# ---------------------------------------------------------------------------
# TestStatisticalTests
# ---------------------------------------------------------------------------


class TestStatisticalTests:
    def test_ttest_significant_difference(self, results_dir: Path):
        t_trials = parse_variant_trials(results_dir / "treatment")
        c_trials = parse_variant_trials(results_dir / "control")
        p = compute_ttest(t_trials, c_trials)
        assert p is not None
        assert p < 0.05

    def test_ttest_no_difference(self):
        t = [TrialResult(trial_name=f"t{i}", reward=0.68 + 0.02 * (i % 3)) for i in range(10)]
        c = [TrialResult(trial_name=f"c{i}", reward=0.65 + 0.01 * i) for i in range(10)]
        p = compute_ttest(t, c)
        assert p is not None
        assert p > 0.05

    def test_ttest_insufficient_data(self):
        t = [TrialResult(trial_name="t1", reward=1.0)]
        c = [TrialResult(trial_name="c1", reward=0.0)]
        assert compute_ttest(t, c) is None

    def test_ttest_error_trials_excluded(self):
        t = [
            TrialResult(trial_name="t1", reward=1.0),
            TrialResult(trial_name="t2", reward=0.9),
            TrialResult(trial_name="t3"),
        ]
        c = [
            TrialResult(trial_name="c1", reward=0.1),
            TrialResult(trial_name="c2", reward=0.0),
        ]
        p = compute_ttest(t, c)
        assert p is not None

    def test_fisher_significant(self):
        t = VariantSummary(n_trials=20, n_passed=18, n_failed=2, pass_rate=0.9)
        c = VariantSummary(n_trials=20, n_passed=5, n_failed=15, pass_rate=0.25)
        p = compute_fisher(t, c)
        assert p is not None
        assert p < 0.001

    def test_fisher_no_difference(self):
        t = VariantSummary(n_trials=20, n_passed=10, n_failed=10, pass_rate=0.5)
        c = VariantSummary(n_trials=20, n_passed=10, n_failed=10, pass_rate=0.5)
        p = compute_fisher(t, c)
        assert p is not None
        assert p > 0.05

    def test_fisher_empty_variant(self):
        t = VariantSummary(n_trials=0)
        c = VariantSummary(n_trials=20, n_passed=10, n_failed=10)
        assert compute_fisher(t, c) is None

    def test_fisher_excludes_error_trials(self):
        t = VariantSummary(n_trials=20, n_passed=10, n_failed=5, n_errors=5)
        c = VariantSummary(n_trials=20, n_passed=10, n_failed=5, n_errors=5)
        p = compute_fisher(t, c)
        assert p is not None
        assert p > 0.05


# ---------------------------------------------------------------------------
# TestBuildAnalysis
# ---------------------------------------------------------------------------


class TestBuildAnalysis:
    def test_full_analysis(self, results_dir: Path):
        result = build_analysis(results_dir, "my-submission")
        assert result.submission_name == "my-submission"
        assert result.summary.treatment.n_trials == 5
        assert result.summary.control.n_trials == 5
        assert result.summary.uplift == pytest.approx(0.4)
        assert result.summary.recommendation == Recommendation.PASS

    def test_threshold_causes_fail(self, results_dir: Path):
        result = build_analysis(results_dir, "my-submission", threshold=0.9)
        assert result.summary.recommendation == Recommendation.FAIL

    def test_threshold_zero_with_no_uplift(self, all_pass_dir: Path):
        result = build_analysis(all_pass_dir, "all-pass", threshold=0.0)
        assert result.summary.uplift == pytest.approx(0.0)
        assert result.summary.recommendation == Recommendation.PASS

    def test_empty_results(self, empty_results_dir: Path):
        result = build_analysis(empty_results_dir, "empty")
        assert result.summary.treatment.n_trials == 0
        assert result.summary.control.n_trials == 0
        assert result.summary.uplift == 0.0
        assert result.summary.recommendation == Recommendation.FAIL

    def test_all_zero_passes_is_fail(self, tmp_path: Path):
        """Both variants have trials but zero passes (e.g. agent exceptions)."""
        base = tmp_path / "all-fail"
        for variant in ("treatment", "control"):
            job = base / variant / f"sub-{variant}"
            trial = job / "trial-0__abc"
            trial.mkdir(parents=True)
            (trial / "result.json").write_text(
                json.dumps(
                    {
                        "verifier_result": {"rewards": {"reward": 0.0}},
                    }
                )
            )
        result = build_analysis(base, "all-fail", threshold=0.0)
        assert result.summary.treatment.n_passed == 0
        assert result.summary.control.n_passed == 0
        assert result.summary.recommendation == Recommendation.FAIL

    def test_provenance_passthrough(self, results_dir: Path):
        prov = Provenance(commit_sha="abc123", pipeline_run_id="run-42")
        result = build_analysis(results_dir, "my-submission", provenance=prov)
        assert result.provenance.commit_sha == "abc123"
        assert result.provenance.pipeline_run_id == "run-42"

    def test_provenance_forge_join_fields(self, results_dir: Path):
        prov = Provenance(
            commit_sha="abc123",
            pipeline_run_url="https://ci.example.com/runs/42",
            ref_name="feat/thing",
            repository_url="https://github.com/o/r",
            change_id="7",
            trace_id="4bf92a",
            eval_run_id="20260722-1",
            harness_fingerprint="deadbeef",
            forge_platform="github",
        )
        result = build_analysis(results_dir, "my-submission", provenance=prov)
        assert result.provenance.repository_url == "https://github.com/o/r"
        assert result.provenance.change_id == "7"
        assert result.provenance.pipeline_run_url == "https://ci.example.com/runs/42"
        assert result.provenance.ref_name == "feat/thing"
        assert result.provenance.harness_fingerprint == "deadbeef"
        assert result.provenance.forge_platform == "github"

    def test_trials_included(self, results_dir: Path):
        result = build_analysis(results_dir, "my-submission")
        assert len(result.trials["treatment"]) == 5
        assert len(result.trials["control"]) == 5

    def test_both_p_values_computed(self, results_dir: Path):
        result = build_analysis(results_dir, "my-submission")
        assert result.summary.ttest_p_value is not None
        assert result.summary.fisher_p_value is not None

    def test_mean_reward_gap(self, results_dir: Path):
        result = build_analysis(results_dir, "my-submission")
        gap = result.summary.mean_reward_gap
        assert gap is not None
        assert gap > 0

    def test_small_sample_warning(self, results_dir: Path, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            build_analysis(results_dir, "my-submission")
        assert any("only 5 trials" in msg for msg in caplog.messages)


# ---------------------------------------------------------------------------
# TestRenderMarkdown
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def test_contains_title(self, results_dir: Path):
        result = build_analysis(results_dir, "my-submission")
        md = render_markdown(result)
        assert "# A/B Evaluation Report: my-submission" in md

    def test_contains_summary_table(self, results_dir: Path):
        result = build_analysis(results_dir, "my-submission")
        md = render_markdown(result)
        assert "| Metric | Treatment | Control |" in md
        assert "Pass Rate" in md

    def test_contains_comparison(self, results_dir: Path):
        result = build_analysis(results_dir, "my-submission")
        md = render_markdown(result)
        assert "Uplift" in md
        assert "t-test" in md
        assert "Fisher" in md

    def test_contains_recommendation(self, results_dir: Path):
        result = build_analysis(results_dir, "my-submission")
        md = render_markdown(result)
        assert "**PASS**" in md

    def test_contains_provenance(self, results_dir: Path):
        prov = Provenance(commit_sha="deadbeef")
        result = build_analysis(results_dir, "my-submission", provenance=prov)
        md = render_markdown(result)
        assert "`deadbeef`" in md

    def test_contains_trial_details(self, results_dir: Path):
        result = build_analysis(results_dir, "my-submission")
        md = render_markdown(result)
        assert "<details>" in md
        assert "Treatment (5 trials)" in md
        assert "Control (5 trials)" in md

    def test_significance_markers(self, results_dir: Path):
        result = build_analysis(results_dir, "my-submission")
        md = render_markdown(result)
        assert "*" in md


# ---------------------------------------------------------------------------
# TestJsonRoundtrip
# ---------------------------------------------------------------------------


class TestJsonRoundtrip:
    def test_json_serialization(self, results_dir: Path):
        result = build_analysis(results_dir, "my-submission")
        json_str = result.model_dump_json(indent=2)
        loaded = AnalysisResult.model_validate_json(json_str)
        assert loaded.submission_name == result.submission_name
        assert loaded.summary.uplift == result.summary.uplift
        assert len(loaded.trials["treatment"]) == len(result.trials["treatment"])


# ---------------------------------------------------------------------------
# TestMainCLI
# ---------------------------------------------------------------------------


class TestMainCLI:
    def test_success(self, results_dir: Path, tmp_path: Path):
        out_dir = tmp_path / "reports"
        rc = main(
            [
                "--results-dir",
                str(results_dir),
                "--output-dir",
                str(out_dir),
                "--submission-name",
                "my-submission",
            ]
        )
        assert rc == 0
        assert (out_dir / "report.json").is_file()
        assert (out_dir / "report.md").is_file()

        report = json.loads((out_dir / "report.json").read_text())
        assert report["submission_name"] == "my-submission"

    def test_with_provenance_flags(self, results_dir: Path, tmp_path: Path):
        out_dir = tmp_path / "reports"
        rc = main(
            [
                "--results-dir",
                str(results_dir),
                "--output-dir",
                str(out_dir),
                "--submission-name",
                "my-submission",
                "--commit-sha",
                "abc123",
                "--pipeline-run-id",
                "run-42",
                "--treatment-image-ref",
                "img@sha256:aaa",
                "--control-image-ref",
                "img@sha256:bbb",
                "--harbor-fork-revision",
                "main",
            ]
        )
        assert rc == 0
        report = json.loads((out_dir / "report.json").read_text())
        assert report["provenance"]["commit_sha"] == "abc123"

    def test_with_threshold(self, results_dir: Path, tmp_path: Path):
        out_dir = tmp_path / "reports"
        rc = main(
            [
                "--results-dir",
                str(results_dir),
                "--output-dir",
                str(out_dir),
                "--submission-name",
                "my-submission",
                "--threshold",
                "0.9",
            ]
        )
        assert rc == 0
        report = json.loads((out_dir / "report.json").read_text())
        assert report["summary"]["recommendation"] == "fail"

    def test_nonexistent_results_dir(self, tmp_path: Path):
        rc = main(
            [
                "--results-dir",
                str(tmp_path / "nope"),
                "--output-dir",
                str(tmp_path / "out"),
                "--submission-name",
                "x",
            ]
        )
        assert rc == 1

    def test_creates_output_dir(self, results_dir: Path, tmp_path: Path):
        out_dir = tmp_path / "nested" / "dir" / "reports"
        rc = main(
            [
                "--results-dir",
                str(results_dir),
                "--output-dir",
                str(out_dir),
                "--submission-name",
                "my-submission",
            ]
        )
        assert rc == 0
        assert (out_dir / "report.json").is_file()

    def test_markdown_written(self, results_dir: Path, tmp_path: Path):
        out_dir = tmp_path / "reports"
        main(
            [
                "--results-dir",
                str(results_dir),
                "--output-dir",
                str(out_dir),
                "--submission-name",
                "my-submission",
            ]
        )
        md = (out_dir / "report.md").read_text()
        assert "# A/B Evaluation Report" in md


# ---------------------------------------------------------------------------
# TestSecurityModels
# ---------------------------------------------------------------------------


class TestSecurityModels:
    """Tests for SecurityFinding and SecurityScanResult Pydantic models."""

    def test_security_finding_basic(self):
        finding = SecurityFinding(
            rule_id="SKILL-001",
            severity=SecuritySeverity.HIGH,
            message="Potential prompt injection",
            file_path="skills/SKILL.md",
            line_number=42,
            scanner="cisco",
        )
        assert finding.rule_id == "SKILL-001"
        assert finding.severity == SecuritySeverity.HIGH
        assert finding.scanner == "cisco"

    def test_security_finding_optional_fields(self):
        finding = SecurityFinding(
            rule_id="SKILL-002",
            severity=SecuritySeverity.MEDIUM,
            message="Suspicious pattern",
            scanner="cisco",
        )
        assert finding.file_path is None
        assert finding.line_number is None

    def test_security_scan_result_empty(self):
        result = SecurityScanResult(
            scanner="cisco",
            scan_mode=ScanMode.WARN,
        )
        assert result.scanner == "cisco"
        assert result.scan_mode == ScanMode.WARN
        assert result.findings == []
        assert result.passed is True

    def test_severity_counts_computed_from_findings(self):
        findings = [
            SecurityFinding(rule_id="R1", severity=SecuritySeverity.CRITICAL, message="m1", scanner="cisco"),
            SecurityFinding(rule_id="R2", severity=SecuritySeverity.HIGH, message="m2", scanner="cisco"),
            SecurityFinding(rule_id="R3", severity=SecuritySeverity.HIGH, message="m3", scanner="cisco"),
            SecurityFinding(rule_id="R4", severity=SecuritySeverity.MEDIUM, message="m4", scanner="cisco"),
            SecurityFinding(rule_id="R5", severity=SecuritySeverity.LOW, message="m5", scanner="cisco"),
            SecurityFinding(rule_id="R6", severity=SecuritySeverity.LOW, message="m6", scanner="cisco"),
            SecurityFinding(rule_id="R7", severity=SecuritySeverity.INFO, message="m7", scanner="cisco"),
        ]
        result = SecurityScanResult(
            scanner="cisco",
            scan_mode=ScanMode.BLOCK,
            findings=findings,
            passed=False,
        )
        counts = result.severity_counts
        assert counts["critical"] == 1
        assert counts["high"] == 2
        assert counts["medium"] == 1
        assert counts["low"] == 2
        assert counts["info"] == 1

    def test_severity_counts_empty_findings(self):
        result = SecurityScanResult(
            scanner="cisco",
            scan_mode=ScanMode.WARN,
            findings=[],
        )
        counts = result.severity_counts
        assert counts == {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}

    def test_security_scan_json_roundtrip(self):
        findings = [
            SecurityFinding(
                rule_id="SKILL-001",
                severity=SecuritySeverity.HIGH,
                message="Test finding",
                file_path="test.md",
                line_number=10,
                scanner="cisco",
            ),
        ]
        result = SecurityScanResult(
            scanner="cisco",
            scan_mode=ScanMode.BLOCK,
            findings=findings,
            passed=False,
            sarif_path="/tmp/scan.sarif",
            json_path="/tmp/scan.json",
            scan_duration_seconds=5.2,
        )
        json_str = result.model_dump_json()
        loaded = SecurityScanResult.model_validate_json(json_str)
        assert loaded.scanner == "cisco"
        assert loaded.scan_mode == ScanMode.BLOCK
        assert len(loaded.findings) == 1
        assert loaded.findings[0].severity == SecuritySeverity.HIGH
        assert loaded.severity_counts["high"] == 1
        assert loaded.passed is False
        assert loaded.scan_duration_seconds == 5.2

    def test_analysis_result_with_security_scans(self, results_dir: Path):
        result = build_analysis(results_dir, "my-submission")
        security_scan = SecurityScanResult(
            scanner="cisco",
            scan_mode=ScanMode.WARN,
            findings=[
                SecurityFinding(
                    rule_id="R1",
                    severity=SecuritySeverity.MEDIUM,
                    message="Warning",
                    scanner="cisco",
                ),
            ],
            passed=True,
        )
        result.security_scans = [security_scan]
        json_str = result.model_dump_json(indent=2)
        loaded = AnalysisResult.model_validate_json(json_str)
        assert len(loaded.security_scans) == 1
        assert loaded.security_scans[0].scanner == "cisco"
        assert loaded.security_scans[0].severity_counts["medium"] == 1
