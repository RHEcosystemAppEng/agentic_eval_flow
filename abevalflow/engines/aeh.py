"""Agent-Eval-Harness (AEH) evaluation engine adapter.

AEH is a generic evaluation framework for agents and skills. This adapter
handles both single-run and pairwise comparison modes.

Result files from AEH:
  - summary.yaml: Per-judge means, per-case results, run metadata, pairwise results
  - run_result.json: Execution metadata (duration, cost, tokens, mean_reward)

The adapter reads report.json (produced by aggregate_aeh.py) or falls back
to reading summary.yaml + run_result.json directly.

Judge types supported (passthrough - no interpretation):
  - check: Inline Python snippets
  - llm: LLM-based judges with prompt/prompt_file
  - builtin: Registry-based judges
  - code: External Python modules
  - pairwise: Position-swapped LLM comparison (in pairwise mode)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from abevalflow.aeh_scoring import (
    DEFAULT_AEH_THRESHOLD,
    numeric_judge_is_low,
    pairwise_outcome,
)
from abevalflow.engines import register_engine
from abevalflow.engines.base import EvalEngine
from abevalflow.gates.base import Finding, GateResult, GateType, Severity
from abevalflow.schemas import GatePolicy

logger = logging.getLogger(__name__)

# Back-compat alias for imports/tests
DEFAULT_PAIRWISE_THRESHOLD = DEFAULT_AEH_THRESHOLD


@register_engine("aeh")
class AEHEngine(EvalEngine):
    """Agent-Eval-Harness evaluation engine.

    Reads AEH output (summary.yaml, run_result.json) or the unified
    report.json produced by aggregate_aeh.py.

    Supports both single-run and pairwise A/B comparison modes.
    """

    name = "aeh"

    def read_result(self, reports_dir: Path) -> dict[str, Any] | None:
        """Read AEH results from reports directory.

        Priority order:
        1. report.json (unified format from aggregate_aeh.py)
        2. summary.yaml + run_result.json (raw AEH output)
        3. Search subdirectories for run outputs

        Args:
            reports_dir: Path to reports/{submission-name}/ or
                         reports/{submission-name}/{run-id}/

        Returns:
            Raw result dict, or None if not found
        """
        report_path = reports_dir / "report.json"
        if report_path.exists():
            try:
                return json.loads(report_path.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.error("Failed to read AEH report.json: %s", e)

        summary_path = reports_dir / "summary.yaml"
        run_result_path = reports_dir / "run_result.json"

        if summary_path.exists():
            try:
                summary = yaml.safe_load(summary_path.read_text())
                run_result = {}
                if run_result_path.exists():
                    run_result = json.loads(run_result_path.read_text())

                return self._merge_aeh_output(summary, run_result, reports_dir)
            except (yaml.YAMLError, json.JSONDecodeError, OSError) as e:
                logger.error("Failed to read AEH output files: %s", e)
                return None

        run_dirs = [d for d in reports_dir.iterdir() if d.is_dir()]
        for run_dir in sorted(run_dirs, reverse=True):
            result = self._read_from_run_dir(run_dir)
            if result is not None:
                return result

        logger.warning("AEH results not found in: %s", reports_dir)
        return None

    def _read_from_run_dir(self, run_dir: Path) -> dict[str, Any] | None:
        """Read AEH results from a specific run directory."""
        summary_path = run_dir / "summary.yaml"
        run_result_path = run_dir / "run_result.json"

        if not summary_path.exists():
            return None

        try:
            summary = yaml.safe_load(summary_path.read_text())
            run_result = {}
            if run_result_path.exists():
                run_result = json.loads(run_result_path.read_text())
            return self._merge_aeh_output(summary, run_result, run_dir)
        except (yaml.YAMLError, json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read AEH run dir %s: %s", run_dir, e)
            return None

    def _merge_aeh_output(
        self,
        summary: dict[str, Any],
        run_result: dict[str, Any],
        source_dir: Path,
    ) -> dict[str, Any]:
        """Merge summary.yaml and run_result.json into unified format."""
        mean_reward = run_result.get("mean_reward")
        if mean_reward is None:
            mean_reward = summary.get("mean_reward")

        # Detect mode from pairwise presence
        mode = "pairwise" if "pairwise" in summary else "single"

        return {
            "eval_engine": "aeh",
            "mode": mode,
            "run_id": summary.get("run_id", source_dir.name),
            "mean_reward": mean_reward,
            "judges": summary.get("judges", {}),
            "per_case": summary.get("per_case", {}),
            "run_metrics": summary.get("run_metrics"),
            "pairwise": summary.get("pairwise"),
            "execution": {
                "duration_s": run_result.get("duration_s"),
                "cost_usd": run_result.get("cost_usd"),
                "tokens": run_result.get("token_usage"),
                "harbor_job_dir": run_result.get("harbor_job_dir"),
            },
            "aeh_warnings": [],
            "source_dir": str(source_dir),
        }

    def to_gate_result(
        self,
        raw_result: dict[str, Any],
        policy: GatePolicy,
    ) -> GateResult:
        """Convert AEH result to GateResult.

        For single-run mode, uses mean_reward vs threshold.
        For pairwise mode, uses win_rate (treatment wins / total) vs threshold.
        """
        mode = raw_result.get("mode", "single")

        if mode == "pairwise":
            return self._handle_pairwise_result(raw_result, policy)

        return self._handle_single_result(raw_result, policy)

    def _handle_single_result(
        self,
        raw_result: dict[str, Any],
        policy: GatePolicy,
    ) -> GateResult:
        """Handle single-run result."""
        gate_policy = policy.get_gate_policy("evaluation")
        threshold = gate_policy.threshold if gate_policy.threshold is not None else self.get_default_threshold()

        mean_reward = raw_result.get("mean_reward")
        if mean_reward is None:
            summary = raw_result.get("summary") or {}
            treatment = summary.get("treatment") or {}
            mean_reward = treatment.get("mean_reward")
        findings = self._extract_findings_single(raw_result)
        judge_errors = [f for f in findings if f.rule_id == "aeh-judge-error"]
        passed = mean_reward is not None and mean_reward >= threshold
        # GateResult.score is a required float; None (judge errors) is not a
        # quality score of 0.0 — call that out in the message.
        score = float(mean_reward) if mean_reward is not None else 0.0

        judges = raw_result.get("judges") or {}
        if not judges:
            summary = raw_result.get("summary") or {}
            judges = summary.get("judges") or {}
        judge_summary = self._format_judge_summary(judges)
        per_case = raw_result.get("per_case") or {}

        if mean_reward is None and judge_errors:
            message = (
                f"AEH single: mean_reward unavailable — {len(judge_errors)} judge "
                f"error(s), not a quality score of 0.0 (threshold={threshold:.2f})"
            )
        else:
            reward_str = f"{mean_reward:.3f}" if mean_reward is not None else "None"
            message = f"AEH single: mean_reward={reward_str} (threshold={threshold:.2f})"
        if judge_summary:
            message += f" | {judge_summary}"

        return GateResult(
            gate_type=GateType.ENGINE,
            gate_name="evaluation",
            policy_key=self.name,
            passed=passed,
            score=score,
            mode=gate_policy.mode,
            threshold=threshold,
            findings=findings,
            details={
                "engine": self.name,
                "mode": "single",
                "comparison": "single",
                "scoring_unavailable": mean_reward is None,
                "judges": judges,
                "per_case": per_case,
                "run_metrics": raw_result.get("run_metrics"),
                "execution": raw_result.get("execution"),
                "aeh_warnings": raw_result.get("aeh_warnings", []),
            },
            message=message,
        )

    def _handle_pairwise_result(
        self,
        raw_result: dict[str, Any],
        policy: GatePolicy,
    ) -> GateResult:
        """Handle pairwise comparison result.

        In pairwise mode:
        - wins_a = treatment wins (treatment is run_a in AEH's compare_runs)
        - wins_b = control wins
        - Win rate = wins_a / (wins_a + wins_b + ties + errors)
        - Ties and errors count as non-wins
        - All-ties (no decisive outcomes, no errors) still passes
        - Default threshold is 0.5 (treatment must win majority of cases)
        """
        gate_policy = policy.get_gate_policy("evaluation")

        # Use default pairwise threshold if not specified
        threshold = gate_policy.threshold
        if threshold is None:
            threshold = DEFAULT_PAIRWISE_THRESHOLD

        pairwise = raw_result.get("pairwise", {})
        outcome = pairwise_outcome(
            pairwise.get("wins_a", 0),
            pairwise.get("wins_b", 0),
            pairwise.get("ties", 0),
            pairwise.get("errors", 0),
            threshold=threshold,
        )
        wins_a = outcome["wins_a"]
        ties = outcome["ties"]
        errors = outcome["errors"]
        total = outcome["total"]
        win_rate = outcome["win_rate"]
        passed = outcome["passed"]

        findings = self._extract_findings_pairwise(raw_result)

        # Include stability info if present
        stability = pairwise.get("stability")
        stability_note = ""
        if stability:
            agreement = stability.get("agreement_rate", 0) * 100
            stability_note = f" | stability={agreement:.0f}%"

        message = (
            f"AEH pairwise: treatment wins {wins_a}/{total} ({win_rate:.0%}) "
            f"| ties={ties}, errors={errors}{stability_note}"
        )

        return GateResult(
            gate_type=GateType.ENGINE,
            gate_name="evaluation",
            policy_key=self.name,
            passed=passed,
            score=win_rate,
            mode=gate_policy.mode,
            threshold=threshold,
            findings=findings,
            details={
                "engine": self.name,
                "mode": "pairwise",
                "pairwise": pairwise,
                "treatment": raw_result.get("treatment"),
                "control": raw_result.get("control"),
                "mean_reward": raw_result.get("mean_reward"),
                "aeh_warnings": raw_result.get("aeh_warnings", []),
            },
            message=message,
        )

    def _format_judge_summary(self, judges: dict[str, Any]) -> str:
        """Format judge results without assuming type.

        Handles all judge types: check, llm, builtin, code.
        Each judge entry can be a dict with mean/pass_rate or a raw value.
        """
        parts = []
        for name, data in judges.items():
            if isinstance(data, dict):
                if "pass_rate" in data and data["pass_rate"] is not None:
                    parts.append(f"{name}={data['pass_rate']:.0%}")
                elif "mean" in data and data["mean"] is not None:
                    parts.append(f"{name}={data['mean']:.2f}")
            elif isinstance(data, (int, float)):
                parts.append(f"{name}={data:.2f}")
        return ", ".join(parts) if parts else ""

    def _extract_findings_single(self, raw_result: dict[str, Any]) -> list[Finding]:
        """Extract findings from single-run per-case results.

        Handles two per_case formats:
        1. Simple format (from old tests/simple aggregation):
           {"case-001": {"reward": 0.3}}
        2. Nested AEH format (from real AEH output):
           {"case-001": {"judge_name": {"value": True/False or 1-5, ...}}}
        """
        findings = []
        per_case = raw_result.get("per_case", {})

        for case_id, case_data in per_case.items():
            if not isinstance(case_data, dict):
                continue

            # Check for simple format first (has 'reward' key directly)
            if "reward" in case_data or "mean_reward" in case_data:
                reward = case_data.get("reward", case_data.get("mean_reward", 1.0))
                if isinstance(reward, (int, float)) and reward < 0.5:
                    severity = Severity.HIGH if reward < 0.25 else Severity.MEDIUM
                    findings.append(
                        Finding(
                            severity=severity,
                            message=f"Case {case_id} scored low: reward={reward:.2f}",
                            location=case_id,
                            rule_id="aeh-low-reward",
                        )
                    )
                continue

            # Nested AEH format - iterate over judges
            for judge_name, judge_result in case_data.items():
                if not isinstance(judge_result, dict):
                    continue

                value = judge_result.get("value")
                error = judge_result.get("error")

                if error:
                    findings.append(
                        Finding(
                            severity=Severity.HIGH,
                            message=f"Case {case_id} judge '{judge_name}' error: {error}",
                            location=f"{case_id}/{judge_name}",
                            rule_id="aeh-judge-error",
                        )
                    )
                elif value is False:
                    findings.append(
                        Finding(
                            severity=Severity.MEDIUM,
                            message=f"Case {case_id} judge '{judge_name}' failed",
                            location=f"{case_id}/{judge_name}",
                            rule_id="aeh-judge-failed",
                        )
                    )
                elif isinstance(value, (int, float)) and not isinstance(value, bool) and numeric_judge_is_low(value):
                    severity = (
                        Severity.HIGH
                        if (isinstance(value, int) and value < 2) or (isinstance(value, float) and value < 0.25)
                        else Severity.MEDIUM
                    )
                    findings.append(
                        Finding(
                            severity=severity,
                            message=f"Case {case_id} judge '{judge_name}' scored low: {value}",
                            location=f"{case_id}/{judge_name}",
                            rule_id="aeh-low-score",
                        )
                    )

        return findings

    def _extract_findings_pairwise(self, raw_result: dict[str, Any]) -> list[Finding]:
        """Extract findings from pairwise comparison results."""
        findings = []
        pairwise = raw_result.get("pairwise", {})
        per_case = pairwise.get("per_case", [])

        for case_result in per_case:
            if not isinstance(case_result, dict):
                continue

            case_id = case_result.get("case_id", "unknown")
            winner = case_result.get("winner")
            error = case_result.get("error")

            if error:
                findings.append(
                    Finding(
                        severity=Severity.HIGH,
                        message=f"Case {case_id} comparison error: {error}",
                        location=case_id,
                        rule_id="aeh-pairwise-error",
                    )
                )
            elif winner == "B":
                findings.append(
                    Finding(
                        severity=Severity.MEDIUM,
                        message=f"Case {case_id}: control beat treatment",
                        location=case_id,
                        rule_id="aeh-control-wins",
                    )
                )

        return findings

    def get_default_threshold(self) -> float:
        """AEH default threshold is 0.5.

        This aligns with the recommendation logic in aggregate_aeh.py.
        For single mode: mean_reward >= 0.5 to pass.
        For pairwise mode: win_rate >= 0.5, or all-ties, to pass.
        """
        return 0.5
