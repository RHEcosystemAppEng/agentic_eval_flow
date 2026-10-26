"""Command interface for run aeh."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from abevalflow.execution.runner import RUNNERS, RunnerError, get_runner


def main() -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Unified AEH runner - abstracts execution backend")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Common arguments
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--runner",
        choices=list(RUNNERS.keys()),
        default="harbor",
        help="AEH execution backend (Harbor on Kubernetes).",
    )
    common.add_argument("--model", help="Model for skill execution")
    common.add_argument("--judge-model", help="Model for LLM judges")
    common.add_argument("--image", help="Container image for trial pods (harbor mode)")
    common.add_argument(
        "--env-type",
        default="kubernetes",
        help="Environment type for harbor mode (default: kubernetes)",
    )

    # Single run command
    single = subparsers.add_parser("single", parents=[common], help="Run single evaluation")
    single.add_argument("--config", required=True, help="Path to eval.yaml")
    single.add_argument("--output", required=True, help="Output directory")
    single.add_argument("--run-id", help="Run identifier")
    single.add_argument("--tasks-dir", help="Tasks directory (harbor mode)")
    single.add_argument("--jobs-dir", help="Jobs directory (harbor mode)")

    # Pairwise run command
    pairwise = subparsers.add_parser("pairwise", parents=[common], help="Run pairwise A/B comparison")
    pairwise.add_argument("--control-config", required=True, help="Path to control eval.yaml")
    pairwise.add_argument("--treatment-config", required=True, help="Path to treatment eval.yaml")
    pairwise.add_argument("--output", required=True, help="Base output directory")
    pairwise.add_argument("--run-id", required=True, help="Run identifier")
    pairwise.add_argument("--score-py-path", help="Path to score.py")
    pairwise.add_argument("--judge", default="pairwise", help="Judge name for comparison")
    pairwise.add_argument("--tasks-dir", help="Tasks directory (harbor mode)")
    pairwise.add_argument("--jobs-dir", help="Jobs directory (harbor mode)")

    args = parser.parse_args()

    try:
        runner = get_runner(
            args.runner,
            model=args.model,
            judge_model=args.judge_model,
            image=getattr(args, "image", None),
            env_type=getattr(args, "env_type", "kubernetes"),
        )

        if args.command == "single":
            exit_code = runner.run_single(
                config=Path(args.config),
                output=Path(args.output),
                run_id=args.run_id,
                tasks_dir=Path(args.tasks_dir) if args.tasks_dir else None,
                jobs_dir=Path(args.jobs_dir) if args.jobs_dir else None,
            )
            return exit_code

        elif args.command == "pairwise":
            result = runner.run_pairwise(
                control_config=Path(args.control_config),
                treatment_config=Path(args.treatment_config),
                output_base=Path(args.output),
                run_id=args.run_id,
                score_py_path=Path(args.score_py_path) if args.score_py_path else None,
                judge=args.judge,
                tasks_dir=Path(args.tasks_dir) if args.tasks_dir else None,
                jobs_dir=Path(args.jobs_dir) if args.jobs_dir else None,
            )

            # Print results summary
            print("\n=== Pairwise Results ===")
            print(f"Control: {result['control_dir']} (exit: {result['control_exit']})")
            print(f"Treatment: {result['treatment_dir']} (exit: {result['treatment_exit']})")
            print(f"Pairwise exit: {result['pairwise_exit']}")

            # Return pairwise exit code (0 = pass, 1 = regression)
            return result["pairwise_exit"]

    except RunnerError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2

    return 0
