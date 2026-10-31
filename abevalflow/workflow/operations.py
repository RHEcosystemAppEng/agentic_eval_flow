"""Operations shared by combined runs and independently invoked container phases."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from abevalflow.config.runtime import RuntimeConfig
from abevalflow.results.run import RunResult, StepResult, write_json


class Operations:
    """Run isolated operations using explicit configuration and a shared artifact layout."""

    def __init__(self, config: RuntimeConfig):
        from abevalflow.workflow.workspace import staged_source_root

        self.config = config
        self.reports = config.output / "reports"
        self.source = staged_source_root(config)
        self.skill = self.source / config.skill.relative_to(config.source)
        self.submission = self.source / config.submission.relative_to(config.source) if config.submission else None

    def execute(self, name: str, result: RunResult) -> StepResult:
        operation = getattr(self, "validate_submission" if name == "submission" else name.replace("-", "_"))
        return operation(result)

    def prepare(self, result: RunResult) -> StepResult:
        from abevalflow.workflow.workspace import stage_source

        digest = stage_source(self.config)
        return StepResult(name="prepare", details={"source_digest": digest})

    def structure(self, result: RunResult) -> StepResult:
        from abevalflow.skill_structure import validate_skill_structure

        check = validate_skill_structure(self.skill)
        path = self.reports / "skill-structure-validation.json"
        write_json(path, check.model_dump(mode="json"))
        return StepResult(
            name="structure",
            tier=1,
            verdict="pass" if check.passed else "fail",
            reason=check.message,
            artifacts=[str(path)],
            details=check.model_dump(mode="json"),
        )

    def security(self, result: RunResult) -> StepResult:
        from abevalflow.validation.scanners import execute_scanner, scanner_command

        output = self.reports / "security-scan.json"
        return execute_scanner(
            "security",
            scanner_command(
                self.config,
                "security",
                [
                    self.config.security_command,
                    "scan",
                    str(self.skill),
                    "--format",
                    "json",
                    "--format",
                    "sarif",
                    "--output-json",
                    str(output),
                    "--output-sarif",
                    str(self.reports / "security-scan.sarif"),
                    "--lenient",
                    "--fail-on-severity",
                    "high",
                ],
            ),
            [output],
            self.reports / "security.log",
            self.config.command_timeout,
        )

    def quality(self, result: RunResult) -> StepResult:
        from abevalflow.validation.scanners import execute_scanner, scanner_command

        outputs = [self.reports / "skillmd-quality-scan.json", self.reports / "skillmd-security-scan.json"]
        return execute_scanner(
            "quality",
            scanner_command(
                self.config,
                "quality",
                [
                    self.config.quality_command,
                    "skill-submission-scan",
                    str(self.skill),
                    "--output-quality",
                    str(outputs[0]),
                    "--output-security",
                    str(outputs[1]),
                ],
            ),
            outputs,
            self.reports / "quality.log",
            self.config.command_timeout,
        )

    def validate_submission(self, result: RunResult) -> StepResult:
        from abevalflow.validation.submission import validate_submission

        metadata = self.config.metadata
        errors = validate_submission(
            self.submission,
            aeh_mode=metadata.aeh_mode.value,
            aeh_control_config=metadata.aeh_control_config,
            aeh_treatment_config=metadata.aeh_treatment_config,
        )
        path = self.reports / "validation.json"
        write_json(path, {"valid": not errors, "errors": errors})
        return StepResult(
            name="submission",
            tier=2,
            verdict="fail" if errors else "pass",
            reason="; ".join(errors),
            artifacts=[str(path)],
        )

    def submission_review(self, result: RunResult) -> StepResult:
        from abevalflow.observability.checkpoint import checkpoint_review
        from abevalflow.review.service import ReviewService

        assessment = ReviewService(self.config).review_submission(self.submission)
        path = self.reports / "_ai_review.json"
        write_json(path, assessment)
        checkpoint_review(self.config.run_id, self.config.name, self.reports, assessment)
        blocking = bool(
            self.config.metadata.gate_policy
            and self.config.metadata.gate_policy.get_gate_policy("quality").mode.value == "block"
        )
        verdict = assessment["recommendation"]
        if blocking:
            from abevalflow.gates.quality.llm_review import LLMReviewGate

            gate = LLMReviewGate().evaluate(self.reports, self.config.metadata.gate_policy)
            verdict = "pass" if gate.passed else "fail"
        return StepResult(
            name="submission-review",
            tier=2,
            blocking=blocking,
            verdict=verdict,
            artifacts=[str(path)],
            details=assessment,
        )

    def skill_review(self, result: RunResult) -> StepResult:
        from abevalflow.observability.checkpoint import checkpoint_review
        from abevalflow.review.service import ReviewService

        assessment = ReviewService(self.config).review_skill(self.skill)
        path = self.reports / "skill-review.json"
        write_json(path, assessment)
        checkpoint_review(self.config.run_id, self.config.name, self.reports, assessment)
        return StepResult(
            name="skill-review",
            tier=1,
            blocking=False,
            verdict=assessment["recommendation"],
            artifacts=[str(path)],
            details=assessment,
        )

    def evaluate(self, result: RunResult) -> StepResult:
        from abevalflow.aeh_scoring import resolve_evaluation_threshold
        from abevalflow.execution.artifacts import eval_dir_name
        from abevalflow.execution.cluster import runner_environment
        from abevalflow.execution.harbor import HarborRunner
        from abevalflow.results.aeh import aggregate_aeh_results

        config = self.config
        metadata = config.metadata
        evaluation_policy = (
            metadata.gate_policy.get_gate_policy("evaluation") if metadata and metadata.gate_policy else None
        )
        minimum_reward = evaluation_policy.minimum_reward if evaluation_policy else None
        evaluation = self.submission / (
            metadata.aeh_treatment_config if metadata.aeh_mode.value == "pairwise" else "eval.yaml"
        )
        raw = yaml.safe_load(evaluation.read_text())
        models = raw.get("models") or {}
        model = config.model or (models if isinstance(models, str) else models.get("skill"))
        judge = config.judge_model or (models.get("judge") if isinstance(models, dict) else None)
        runner = HarborRunner(
            model=model,
            judge_model=judge,
            image=config.trial_image,
            environment=runner_environment(config, self.reports),
            timeout=config.command_timeout,
            aeh_root=config.aeh_root,
            aeh_python=config.aeh_python,
            timeout_retries=config.timeout_retries,
            n_concurrent=config.n_concurrent,
        )
        tasks = config.output / "tasks"
        jobs = config.output / "jobs"
        threshold = resolve_evaluation_threshold(self.submission / "metadata.yaml")
        if metadata.aeh_mode.value == "pairwise":
            control = self.submission / metadata.aeh_control_config
            execution = runner.run_pairwise(
                control,
                evaluation,
                self.reports,
                config.run_id,
                score_py_path=config.score_script,
                tasks_dir=tasks,
                jobs_dir=jobs,
            )
            report = aggregate_aeh_results(
                Path(execution["treatment_dir"]),
                mode="pairwise",
                control_dir=Path(execution["control_dir"]),
                submission_name=config.name,
                threshold=threshold,
                minimum_reward=minimum_reward,
                treatment_config_path=evaluation,
                control_config_path=control,
            )
            codes = [execution["control_exit"], execution["treatment_exit"], execution["pairwise_exit"]]
        else:
            output = self.reports / eval_dir_name(evaluation, config.name) / config.run_id
            code = runner.run_single(evaluation, output, config.run_id, tasks_dir=tasks, jobs_dir=jobs)
            report = aggregate_aeh_results(
                output, submission_name=config.name, threshold=threshold, config_path=evaluation
            )
            codes = [code]
        path = self.reports / "report.json"
        report.setdefault("provenance", {}).update(
            commit_sha=config.revision,
            pipeline_run_id=config.run_id,
            plugin_name=config.plugin,
            built_image_ref=config.built_image_ref,
        )
        write_json(path, report)
        if any(code not in (0, 1) for code in codes):
            return StepResult(
                name="evaluate",
                tier=2,
                status="error",
                verdict="unavailable",
                reason=f"AEH execution failed with exit codes {codes}",
                artifacts=[str(path)],
            )
        recommendation = report.get("summary", {}).get("recommendation", "fail")
        blocking = not metadata.gate_policy or metadata.gate_policy.get_gate_policy("evaluation").mode.value == "block"
        return StepResult(
            name="evaluate",
            tier=2,
            blocking=blocking,
            verdict=recommendation,
            artifacts=[str(path)],
            details={"exit_codes": codes, "mode": metadata.aeh_mode.value, "model": model, "judge_model": judge},
        )

    def report(self, result: RunResult) -> StepResult:
        from abevalflow.certification import compute_certification, load_profile
        from abevalflow.engines.aeh import AEHEngine
        from abevalflow.gates.base import Finding, GateMode, GateResult, GateType, Severity
        from abevalflow.operational_policy import check_operational_policy
        from abevalflow.reporting.markdown import render_markdown
        from abevalflow.reporting.scorecard import write_certification, write_scorecard
        from abevalflow.schemas import GatePolicy
        from abevalflow.scorecard import Scorecard, apply_combination_logic
        from abevalflow.scoring import score_scan_findings
        from abevalflow.skill_structure import load_skill_structure_result

        metadata = self.config.metadata
        policy = metadata.gate_policy if metadata and metadata.gate_policy else GatePolicy()
        gates = []
        for step in result.steps:
            if step.tier is None or step.status == "skipped":
                continue
            if step.name == "evaluate" and step.status == "completed":
                adapter = AEHEngine()
                gates.append(adapter.to_gate_result(json.loads((self.reports / "report.json").read_text()), policy))
                continue
            category = "security" if step.name == "security" else "quality"
            if step.name in {"structure", "submission"}:
                category = "validation"
            scanner_scoring = None
            if step.name in {"security", "quality"}:
                evidence_complete = step.status == "completed" and step.details.get("evidence_complete", True)
                score, scanner_scoring = score_scan_findings(
                    step.details.get("findings", []), evidence_complete=evidence_complete
                )
            else:
                score = step.details.get("overall_score", 1.0 if step.verdict == "pass" else 0.0)
            gate_details = {
                "execution_status": step.status,
                "evidence_complete": step.status == "completed",
                **step.details,
            }
            if scanner_scoring is not None:
                gate_details["scoring"] = scanner_scoring
            gates.append(
                GateResult(
                    gate_type=(
                        GateType.VALIDATION
                        if category == "validation"
                        else (GateType.SECURITY if category == "security" else GateType.QUALITY)
                    ),
                    gate_name=category,
                    policy_key={
                        "security": "cisco",
                        "quality": "skillmd-quality",
                        "submission-review": "llm-review",
                    }.get(step.name, step.name),
                    passed=step.status == "completed" and step.verdict != "fail",
                    score=score if score is not None else 0.0,
                    mode=(
                        policy.get_gate_policy("quality").mode
                        if step.name == "submission-review"
                        else (GateMode.BLOCK if step.blocking else GateMode.WARN)
                    ),
                    message=step.reason,
                    findings=[
                        Finding(
                            severity=Severity(str(f.get("severity", "info")).lower()),
                            message=str(
                                f.get("message") or f.get("description") or f.get("title") or "Scanner finding"
                            ),
                            rule_id=str(f.get("rule_id") or f.get("id") or "scanner"),
                            location=str(f.get("file_path") or f.get("location") or ""),
                            details=f,
                        )
                        for f in step.details.get("findings", [])
                    ],
                    details=gate_details,
                )
            )
        recorded = {step.name for step in result.steps}
        missing = [name for name in result.requested_steps if name != "report" and name not in recorded]
        if missing:
            gates.append(
                GateResult(
                    gate_type=GateType.QUALITY,
                    gate_name="execution",
                    policy_key="workflow",
                    passed=False,
                    score=0,
                    mode=GateMode.BLOCK,
                    message="Missing selected phases: " + ", ".join(missing),
                    details={"evidence_complete": False},
                )
            )
        certification_policy = (
            metadata.certification_policy if metadata and metadata.certification_policy else load_profile()
        )
        structure = load_skill_structure_result(self.reports / "skill-structure-validation.json")
        submission_valid = any(s.name == "submission" and s.verdict == "pass" for s in result.steps)
        certification = compute_certification(
            gates=gates,
            validation_passed=submission_valid,
            metadata_valid=metadata is not None,
            has_eval_assets=self.submission is not None,
            skill_structure_result=structure,
            policy=certification_policy,
            operational_policy_result=check_operational_policy(
                self.submission or self.skill, limits=certification_policy.get_operational_limits()
            ),
        )
        recommendation, reason = apply_combination_logic(gates, policy)
        scorecard = Scorecard(
            submission_name=self.config.name,
            pipeline_run_id=self.config.run_id,
            eval_engine="aeh",
            gates=gates,
            policy=policy,
            recommendation=recommendation,
            recommendation_reason=reason,
            evidence_complete=result.evidence_complete,
            certification=certification,
            provenance={
                "commit_sha": self.config.revision,
                "plugin_name": self.config.plugin,
                "built_image_ref": self.config.built_image_ref,
                "evaluator_version": result.evaluator_version,
                "source_digest": next(
                    (s.details.get("source_digest") for s in result.steps if s.name == "prepare"), None
                ),
            },
        )
        if scorecard.policy.push_facts:
            scorecard.policy = scorecard.policy.model_copy(deep=True)
            scorecard.policy.push_facts.bearer_token = "${COMPASS_API_TOKEN}"
        write_scorecard(scorecard, self.reports)
        write_certification(scorecard, self.reports)
        report_path = self.reports / "report.json"
        if report_path.is_file():
            from abevalflow.report import AnalysisResult

            (self.reports / "report.md").write_text(
                render_markdown(AnalysisResult.model_validate_json(report_path.read_text()))
            )
        summary = [
            f"# {self.config.name}",
            f"Run: {self.config.run_id}",
            f"Recommendation: {recommendation.value}",
            "",
            "| Step | Status | Verdict | Reason |",
            "| --- | --- | --- | --- |",
        ]
        summary += [f"| {s.name} | {s.status} | {s.verdict} | {s.reason.replace('|', '/')} |" for s in result.steps]
        (self.reports / "scorecard.md").write_text("\n".join(summary) + "\n")
        return StepResult(
            name="report", artifacts=[str(self.reports / "scorecard.json"), str(self.reports / "scorecard.md")]
        )
