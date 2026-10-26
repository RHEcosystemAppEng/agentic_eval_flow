"""Command interface for aggregate scorecard."""

from __future__ import annotations

import argparse
from pathlib import Path

from abevalflow.policy.aggregation import aggregate_scorecard, logger, write_certification, write_scorecard


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate gate results into unified scorecard")
    parser.add_argument(
        "--submission-dir",
        type=Path,
        required=True,
        help="Path to submissions/{submission-name}/",
    )
    parser.add_argument(
        "--results-dir",
        type=Path,
        required=True,
        help="Path to eval-results/{submission-name}/",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        required=True,
        help="Path to reports/{submission-name}/",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        required=True,
        help="Path to workspace root (for _ai_review.json)",
    )
    parser.add_argument(
        "--eval-engine",
        type=str,
        required=True,
        choices=["aeh"],
        help="Primary evaluation engine",
    )
    parser.add_argument(
        "--pipeline-run-id",
        type=str,
        default="unknown",
        help="Tekton PipelineRun ID",
    )
    parser.add_argument(
        "--output-tekton-results",
        type=Path,
        default=None,
        help="Directory to write Tekton result files",
    )
    parser.add_argument(
        "--certification-profile",
        type=str,
        default=None,
        help="Skill certification profile. "
        "Provides default checks per artifact type. Overridden by submission's metadata.yaml.",
    )

    args = parser.parse_args()

    from abevalflow.observability.otel import get_tracer, setup_tracer_provider, shutdown_tracer_provider

    setup_tracer_provider()
    tracer = get_tracer("abevalflow.scorecard")

    try:
        with tracer.start_as_current_span(
            "pipeline.aggregate_scorecard",
            attributes={
                "pipeline.run_id": args.pipeline_run_id,
                "pipeline.eval_engine": args.eval_engine,
                "submission.name": args.submission_dir.name,
            },
        ):
            scorecard = aggregate_scorecard(
                submission_dir=args.submission_dir,
                results_dir=args.results_dir,
                reports_dir=args.reports_dir,
                workspace_root=args.workspace_root,
                eval_engine=args.eval_engine,
                pipeline_run_id=args.pipeline_run_id,
                certification_profile=args.certification_profile,
            )

            from abevalflow.persistence.compass import publish_scorecard

            publish_scorecard(scorecard)
            write_scorecard(scorecard, args.reports_dir)
            write_certification(scorecard, args.reports_dir)

            if args.output_tekton_results:
                results_dir = args.output_tekton_results
                results_dir.mkdir(parents=True, exist_ok=True)

                (results_dir / "scorecard-recommendation").write_text(scorecard.recommendation.value)
                (results_dir / "scorecard-gates-passed").write_text(str(scorecard.gates_passed))
                (results_dir / "scorecard-gates-failed").write_text(str(scorecard.gates_failed))
                (results_dir / "scorecard-blocking-passed").write_text(str(scorecard.blocking_gates_passed))
                (results_dir / "scorecard-blocking-failed").write_text(str(scorecard.blocking_gates_failed))
                (results_dir / "highest-certification").write_text(scorecard.highest_certification.value)

                logger.info("Wrote Tekton results to %s", results_dir)

            print(f"Scorecard recommendation: {scorecard.recommendation.value}")
            print(f"Reason: {scorecard.recommendation_reason}")
            print(f"Gates: {scorecard.gates_passed} passed, {scorecard.gates_failed} failed")
            print(f"Highest certification: {scorecard.highest_certification.value}")

        return 0

    except Exception as e:
        logger.exception("Failed to aggregate scorecard: %s", e)
        return 1
    finally:
        shutdown_tracer_provider()
