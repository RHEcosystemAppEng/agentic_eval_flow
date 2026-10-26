"""Command interface for validate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from abevalflow.schemas import EvalEngine
from abevalflow.validation.submission import EVAL_ENGINE_CLI_CHOICES, validate_submission


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a submission directory")
    parser.add_argument("submission_dir", type=Path, help="Path to the submission directory")
    parser.add_argument(
        "--eval-engine",
        type=str,
        choices=list(EVAL_ENGINE_CLI_CHOICES),
        default="aeh",
        help="Evaluation engine (controls which checks run)",
    )
    parser.add_argument(
        "--aeh-mode",
        type=str,
        choices=["single", "pairwise"],
        default="single",
        help="AEH evaluation mode (single or pairwise)",
    )
    parser.add_argument(
        "--aeh-control-config",
        type=str,
        default="eval-control.yaml",
        help="Control variant eval.yaml filename for AEH pairwise mode",
    )
    parser.add_argument(
        "--aeh-treatment-config",
        type=str,
        default="eval-treatment.yaml",
        help="Treatment variant eval.yaml filename for AEH pairwise mode",
    )
    args = parser.parse_args(argv)

    submission_dir: Path = args.submission_dir
    if not submission_dir.is_dir():
        result = {"valid": False, "errors": [f"Not a directory: {submission_dir}"]}
        print(json.dumps(result, indent=2))
        return 1

    engine = EvalEngine(args.eval_engine)
    errors = validate_submission(
        submission_dir,
        eval_engine=engine,
        aeh_mode=args.aeh_mode,
        aeh_control_config=args.aeh_control_config,
        aeh_treatment_config=args.aeh_treatment_config,
    )
    result = {"valid": len(errors) == 0, "errors": errors}
    print(json.dumps(result, indent=2))
    return 0 if result["valid"] else 1
