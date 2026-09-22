"""Tests for scripts/run_aeh.py - AEH runner dispatcher."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from scripts.run_aeh import (
    RUNNERS,
    BaseRunner,
    HarborRunner,
    RunnerError,
    VanillaRunner,
    get_runner,
    materialize_aeh_case_outputs,
)


class TestRunnerRegistry:
    """Test runner registration and lookup."""

    def test_harbor_runner_registered(self):
        """Harbor runner should be registered."""
        assert "harbor" in RUNNERS
        assert RUNNERS["harbor"] is HarborRunner

    def test_vanilla_runner_registered(self):
        """Vanilla runner should be registered."""
        assert "vanilla" in RUNNERS
        assert RUNNERS["vanilla"] is VanillaRunner

    def test_get_runner_harbor(self):
        """get_runner should return HarborRunner for 'harbor'."""
        runner = get_runner("harbor")
        assert isinstance(runner, HarborRunner)

    def test_get_runner_vanilla(self):
        """get_runner should return VanillaRunner for 'vanilla'."""
        runner = get_runner("vanilla")
        assert isinstance(runner, VanillaRunner)

    def test_get_runner_unknown_raises(self):
        """get_runner should raise ValueError for unknown runner."""
        try:
            get_runner("unknown")
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "unknown" in str(e).lower()
            assert "harbor" in str(e).lower()
            assert "vanilla" in str(e).lower()

    def test_get_runner_passes_kwargs(self):
        """get_runner should pass kwargs to runner constructor."""
        runner = get_runner("harbor", model="test-model", judge_model="test-judge")
        assert runner.model == "test-model"
        assert runner.judge_model == "test-judge"


class TestMaterializeAehCaseOutputs:
    """Bridge Harbor verifier/output → run_dir/cases/<id>/output."""

    def test_copies_verifier_output(self, tmp_path: Path):
        config = tmp_path / "eval.yaml"
        config.write_text(
            yaml.dump(
                {
                    "skill": "demo",
                    "outputs": [{"path": "output"}],
                }
            )
        )
        jobs = tmp_path / "jobs" / "job-1"
        trial = jobs / "case-001__AbCdEf"
        src = trial / "verifier" / "output"
        src.mkdir(parents=True)
        (src / "greeting.txt").write_text("Hello, World!")

        out = tmp_path / "run-out"
        n = materialize_aeh_case_outputs(config, out, jobs_dir=tmp_path / "jobs")
        assert n == 1
        copied = out / "cases" / "case-001" / "output" / "greeting.txt"
        assert copied.is_file()
        assert copied.read_text() == "Hello, World!"

    def test_falls_back_to_artifacts(self, tmp_path: Path):
        config = tmp_path / "eval.yaml"
        config.write_text(yaml.dump({"skill": "demo", "outputs": [{"path": "output"}]}))
        jobs = tmp_path / "jobs" / "job-1"
        trial = jobs / "case-001"
        art = trial / "verifier" / "artifacts"
        art.mkdir(parents=True)
        (art / "out.txt").write_text("x")
        out = tmp_path / "run-out"
        n = materialize_aeh_case_outputs(config, out, jobs_dir=tmp_path / "jobs")
        assert n == 1
        assert (out / "cases" / "case-001" / "output" / "out.txt").read_text() == "x"


class TestHarborRunner:
    """Test HarborRunner execution."""

    def test_harbor_runner_name(self):
        """Harbor runner should have correct name."""
        runner = HarborRunner()
        assert runner.name == "harbor"

    def test_harbor_runner_default_env_type(self):
        """Harbor runner should default to kubernetes env."""
        runner = HarborRunner()
        assert runner.env_type == "kubernetes"

    def test_harbor_runner_custom_env_type(self):
        """Harbor runner should accept custom env type."""
        runner = HarborRunner(env_type="podman")
        assert runner.env_type == "podman"

    @patch("subprocess.run")
    def test_harbor_runner_execute_basic(self, mock_run, tmp_path):
        """Harbor runner should call harbor.run with correct args."""
        mock_run.return_value = MagicMock(returncode=0)
        runner = HarborRunner(model="test-model")

        config = tmp_path / "eval.yaml"
        config.write_text(yaml.dump({"skill": "demo", "models": {"skill": "m"}}))
        output = tmp_path / "output"

        exit_code = runner.run_single(config, output)

        assert exit_code == 0
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]

        assert "-m" in call_args
        assert "agent_eval.harbor.run" in call_args
        assert "--config" in call_args
        assert "--output" in call_args
        # kubernetes mode uses OpenShiftEnvironment import path (not --env)
        assert "--environment-import-path" in call_args
        assert "OpenShiftEnvironment" in " ".join(call_args)
        assert "--model" in call_args
        assert "test-model" in call_args

    @patch("subprocess.run")
    def test_harbor_runner_execute_with_image(self, mock_run, tmp_path):
        """Harbor runner should pass image flag when set."""
        mock_run.return_value = MagicMock(returncode=0)
        runner = HarborRunner(model="test-model", image="quay.io/test/image:v1")

        config = tmp_path / "eval.yaml"
        config.write_text(yaml.dump({"skill": "demo", "models": {"skill": "m"}}))
        output = tmp_path / "output"

        runner.run_single(config, output)

        call_args = mock_run.call_args[0][0]
        assert "--image" in call_args
        assert "quay.io/test/image:v1" in call_args

    @patch("subprocess.run")
    def test_harbor_runner_prepares_enriched_tasks(self, mock_run, tmp_path):
        """With image + tasks_dir, runner generates and enriches Harbor tasks."""
        mock_run.return_value = MagicMock(returncode=0)
        runner = HarborRunner(model="test-model", image="quay.io/test/image:v1")
        config = tmp_path / "eval.yaml"
        config.write_text(yaml.dump({"skill": "demo", "models": {"skill": "m"}}))
        output = tmp_path / "output"
        tasks_dir = tmp_path / "tasks"

        with patch.object(runner, "_prepare_enriched_tasks") as prepare:
            runner.run_single(config, output, tasks_dir=tasks_dir)

        prepare.assert_called_once()
        assert prepare.call_args[0][1] == tasks_dir

    def test_harbor_runner_requires_model(self):
        """Harbor runner should fail fast without model."""
        runner = HarborRunner()  # No model
        config = Path("/tmp/eval.yaml")
        output = Path("/tmp/output")

        try:
            with patch.object(Path, "mkdir"):
                runner.run_single(config, output)
            assert False, "Should have raised RunnerError"
        except RunnerError as e:
            assert "model" in str(e).lower()


class TestVanillaRunner:
    """Test VanillaRunner execution."""

    def test_vanilla_runner_name(self):
        """Vanilla runner should have correct name."""
        runner = VanillaRunner()
        assert runner.name == "vanilla"

    def test_vanilla_runner_raises_runner_error(self, tmp_path):
        """Vanilla runner should raise RunnerError with helpful message."""
        runner = VanillaRunner()
        config = tmp_path / "eval.yaml"
        config.write_text("skill: test")
        output = tmp_path / "output"

        try:
            runner.run_single(config, output)
            assert False, "Should have raised RunnerError"
        except RunnerError as e:
            # Verify helpful message
            msg = str(e).lower()
            assert "not yet implemented" in msg
            assert "harbor" in msg  # Should suggest using harbor instead


class TestBaseRunner:
    """Test BaseRunner common functionality."""

    def test_read_skill_name_from_config(self, tmp_path):
        """_read_skill_name should extract skill from eval.yaml."""
        config_path = tmp_path / "eval.yaml"
        config_path.write_text(yaml.dump({"skill": "my-test-skill"}))

        runner = HarborRunner()
        skill_name = runner._read_skill_name(config_path)

        assert skill_name == "my-test-skill"

    def test_read_skill_name_fallback_to_stem(self, tmp_path):
        """_read_skill_name should fall back to filename stem if no skill field."""
        config_path = tmp_path / "my-config.yaml"
        config_path.write_text(yaml.dump({"models": {"skill": "claude"}}))

        runner = HarborRunner()
        skill_name = runner._read_skill_name(config_path)

        assert skill_name == "my-config"

    def test_read_skill_name_handles_missing_file(self, tmp_path):
        """_read_skill_name should fall back to stem for missing file."""
        config_path = tmp_path / "missing.yaml"

        runner = HarborRunner()
        skill_name = runner._read_skill_name(config_path)

        assert skill_name == "missing"

    def test_read_skill_name_uses_name_when_skill_absent(self, tmp_path):
        """Prompt-mode evals name the reports leaf from eval.yaml name."""
        config_path = tmp_path / "eval.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "name": "forge-eval-rubrics",
                    "execution": {"prompt": "{{ input.prompt }}"},
                }
            )
        )

        runner = HarborRunner()
        assert runner._read_skill_name(config_path) == "forge-eval-rubrics"

    def test_read_skill_name_prefers_skill_over_name(self, tmp_path):
        config_path = tmp_path / "eval.yaml"
        config_path.write_text(
            yaml.dump({"skill": "openclaw-forge", "name": "forge-eval-rubrics"})
        )

        runner = HarborRunner()
        assert runner._read_skill_name(config_path) == "openclaw-forge"

    def test_read_skill_name_uses_execution_skill(self, tmp_path):
        config_path = tmp_path / "eval.yaml"
        config_path.write_text(
            yaml.dump(
                {
                    "name": "forge-eval-rubrics",
                    "execution": {"skill": "rfe.create", "mode": "case"},
                }
            )
        )

        runner = HarborRunner()
        assert runner._read_skill_name(config_path) == "rfe.create"


class TestEvalDirName:
    def test_fallback_submission_name(self, tmp_path):
        from scripts.run_aeh import eval_dir_name

        config_path = tmp_path / "eval.yaml"
        config_path.write_text(yaml.dump({"execution": {"prompt": "hi"}}))
        assert eval_dir_name(config_path, fallback="openclaw-forge") == "openclaw-forge"

    def test_sanitizes_name(self, tmp_path):
        from scripts.run_aeh import eval_dir_name

        config_path = tmp_path / "eval.yaml"
        config_path.write_text(yaml.dump({"name": "Forge Eval Rubrics"}))
        assert eval_dir_name(config_path, fallback="sub") == "forge-eval-rubrics"

    def test_resolve_prefers_eval_name_then_submission(self, tmp_path):
        from scripts.run_aeh import resolve_aeh_run_dir

        reports = tmp_path / "reports"
        run_id = "aeh-openshell-forge-nsenter-nfz69"
        actual = reports / "forge-eval-rubrics" / run_id
        actual.mkdir(parents=True)
        (actual / "summary.yaml").write_text("mean_reward: 1.0\n")
        (reports / "openclaw-forge" / run_id).mkdir(parents=True)

        found = resolve_aeh_run_dir(
            reports,
            run_id,
            eval_name="forge-eval-rubrics",
            submission_name="openclaw-forge",
        )
        assert found == actual

    def test_resolve_falls_back_to_submission_dir(self, tmp_path):
        from scripts.run_aeh import resolve_aeh_run_dir

        reports = tmp_path / "reports"
        run_id = "run-1"
        sub = reports / "openclaw-forge" / run_id
        sub.mkdir(parents=True)
        (sub / "run_result.json").write_text("{}")

        found = resolve_aeh_run_dir(
            reports,
            run_id,
            eval_name="forge-eval-rubrics",
            submission_name="openclaw-forge",
        )
        assert found == sub


class TestPairwiseExecution:
    """Test pairwise A/B comparison execution."""

    @patch("subprocess.run")
    def test_pairwise_creates_correct_directories(self, mock_run, tmp_path):
        """run_pairwise should create control and treatment directories."""
        mock_run.return_value = MagicMock(returncode=0)

        control_config = tmp_path / "eval-control.yaml"
        treatment_config = tmp_path / "eval-treatment.yaml"
        output_base = tmp_path / "output"

        control_config.write_text(yaml.dump({"skill": "test-skill"}))
        treatment_config.write_text(yaml.dump({"skill": "test-skill"}))

        runner = HarborRunner(model="test-model")

        # Mock summary.yaml creation
        def create_summary(*args, **kwargs):
            result = MagicMock(returncode=0)
            # Create summary.yaml after "execution"
            control_dir = output_base / "test-skill" / "control-run-123"
            treatment_dir = output_base / "test-skill" / "treatment-run-123"
            control_dir.mkdir(parents=True, exist_ok=True)
            treatment_dir.mkdir(parents=True, exist_ok=True)
            (control_dir / "summary.yaml").write_text(yaml.dump({"mean_reward": 0.5}))
            (treatment_dir / "summary.yaml").write_text(yaml.dump({"mean_reward": 0.6}))
            return result

        mock_run.side_effect = create_summary

        # Mock score.py path
        score_py = tmp_path / "score.py"
        score_py.write_text("# mock")

        result = runner.run_pairwise(
            control_config=control_config,
            treatment_config=treatment_config,
            output_base=output_base,
            run_id="run-123",
            score_py_path=score_py,
        )

        assert "control_dir" in result
        assert "treatment_dir" in result
        assert "control-run-123" in result["control_dir"]
        assert "treatment-run-123" in result["treatment_dir"]

    @patch("subprocess.run")
    def test_pairwise_regenerates_report_html_with_baseline(self, mock_run, tmp_path):
        """After pairwise, call report.py --baseline so HTML includes pairwise."""
        mock_run.return_value = MagicMock(returncode=0)

        control_config = tmp_path / "eval-control.yaml"
        treatment_config = tmp_path / "eval-treatment.yaml"
        output_base = tmp_path / "output"
        control_config.write_text(yaml.dump({"skill": "test-skill"}))
        treatment_config.write_text(yaml.dump({"skill": "test-skill"}))

        score_py = tmp_path / "score.py"
        score_py.write_text("# mock")
        report_py = tmp_path / "report.py"
        report_py.write_text("# mock")

        def create_summary(*args, **kwargs):
            control_dir = output_base / "test-skill" / "control-run-123"
            treatment_dir = output_base / "test-skill" / "treatment-run-123"
            control_dir.mkdir(parents=True, exist_ok=True)
            treatment_dir.mkdir(parents=True, exist_ok=True)
            (control_dir / "summary.yaml").write_text(yaml.dump({"mean_reward": 0.5}))
            (treatment_dir / "summary.yaml").write_text(
                yaml.dump({"mean_reward": 0.6, "pairwise": {"wins_a": 0, "ties": 1}})
            )
            return MagicMock(returncode=0)

        mock_run.side_effect = create_summary
        runner = HarborRunner(model="test-model")

        # HarborRunner._execute also uses subprocess; stub it so only pairwise+report run.
        with patch.object(HarborRunner, "_execute", return_value=0):
            # Still need summary.yaml present before pairwise — create them up front.
            control_dir = output_base / "test-skill" / "control-run-123"
            treatment_dir = output_base / "test-skill" / "treatment-run-123"
            control_dir.mkdir(parents=True, exist_ok=True)
            treatment_dir.mkdir(parents=True, exist_ok=True)
            (control_dir / "summary.yaml").write_text(yaml.dump({"mean_reward": 0.5}))
            (treatment_dir / "summary.yaml").write_text(yaml.dump({"mean_reward": 0.6}))

            runner.run_pairwise(
                control_config=control_config,
                treatment_config=treatment_config,
                output_base=output_base,
                run_id="run-123",
                score_py_path=score_py,
            )

        cmds = [" ".join(str(x) for x in call.args[0]) for call in mock_run.call_args_list]
        report_cmds = [c for c in cmds if "report.py" in c]
        assert report_cmds, f"expected report.py invoke, got: {cmds}"
        assert "--baseline" in report_cmds[0]
        assert "control-run-123" in report_cmds[0]
        assert "treatment-run-123" in report_cmds[0]

    def test_pairwise_fails_fast_on_missing_summary(self, tmp_path):
        """run_pairwise should fail fast if control produces no summary.yaml."""
        control_config = tmp_path / "eval-control.yaml"
        treatment_config = tmp_path / "eval-treatment.yaml"
        output_base = tmp_path / "output"

        control_config.write_text(yaml.dump({"skill": "test-skill"}))
        treatment_config.write_text(yaml.dump({"skill": "test-skill"}))

        runner = HarborRunner(model="test-model")

        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            try:
                runner.run_pairwise(
                    control_config=control_config,
                    treatment_config=treatment_config,
                    output_base=output_base,
                    run_id="run-123",
                )
                assert False, "Should have raised RunnerError"
            except RunnerError as e:
                assert "summary.yaml" in str(e).lower()


class TestCLI:
    """Test CLI argument parsing."""

    def test_cli_single_missing_config_fails(self, tmp_path):
        """CLI single command should fail without --config."""
        import sys
        from unittest.mock import patch as mock_patch

        from scripts.run_aeh import main

        output = tmp_path / "output"

        with mock_patch.object(
            sys,
            "argv",
            ["run_aeh.py", "single", "--output", str(output)],
        ):
            try:
                main()
            except SystemExit as e:
                # argparse exits with 2 for missing required args
                assert e.code == 2

    def test_cli_pairwise_missing_configs_fails(self, tmp_path):
        """CLI pairwise command should fail without control/treatment configs."""
        import sys
        from unittest.mock import patch as mock_patch

        from scripts.run_aeh import main

        output = tmp_path / "output"

        with mock_patch.object(
            sys,
            "argv",
            ["run_aeh.py", "pairwise", "--output", str(output), "--run-id", "test"],
        ):
            try:
                main()
            except SystemExit as e:
                # argparse exits with 2 for missing required args
                assert e.code == 2

    def test_cli_harbor_requires_model(self, tmp_path):
        """CLI harbor runner should fail without --model."""
        import sys
        from unittest.mock import patch as mock_patch

        from scripts.run_aeh import main

        config = tmp_path / "eval.yaml"
        config.write_text(yaml.dump({"skill": "test"}))
        output = tmp_path / "output"

        with mock_patch.object(
            sys,
            "argv",
            [
                "run_aeh.py",
                "single",
                "--runner",
                "harbor",
                "--config",
                str(config),
                "--output",
                str(output),
            ],
        ):
            # Should fail because harbor requires --model
            exit_code = main()
            assert exit_code == 1  # RunnerError exits with 1


class TestRunnerExtensibility:
    """Test that new runners can be added easily."""

    def test_custom_runner_registration(self):
        """Custom runners can be added to RUNNERS dict."""

        class CustomRunner(BaseRunner):
            name = "custom"

            def _execute(self, config, output, **opts):
                return 0

        # Register
        RUNNERS["custom"] = CustomRunner

        try:
            runner = get_runner("custom")
            assert isinstance(runner, CustomRunner)
            assert runner.name == "custom"
        finally:
            # Cleanup
            del RUNNERS["custom"]
