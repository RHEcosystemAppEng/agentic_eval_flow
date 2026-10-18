#!/usr/bin/env python3
"""Exclude the reserved pairwise judge from AEH's numeric-range warning.

The pinned harness excludes `pairwise` from per-case numeric scoring and
compares reports with a categorical A/B/tie verdict. Its config loader still
warns that this judge needs a numeric score_range. Patch only that warning;
ordinary numeric judges retain their validation and warnings.

Applied by Containerfile to the pinned upstream config.py. Fail on an
unexpected source version so an upstream change requires review.
"""

import sys
from pathlib import Path

ORIGINAL = """            if (jc.feedback_type in ("int", "float", "") and not jc.score_range
                    and not jc.builtin
                    and (jc.prompt or jc.prompt_file or jc.llm_rubric)):"""
PATCHED = """            if (jc.feedback_type in ("int", "float", "") and not jc.score_range
                    and jc.name != "pairwise" and not jc.builtin
                    and (jc.prompt or jc.prompt_file or jc.llm_rubric)):"""


def main() -> None:
    target = Path(sys.argv[1])
    source = target.read_text()
    if source.count(PATCHED) == 1 and ORIGINAL not in source:
        return
    if source.count(ORIGINAL) != 1:
        raise SystemExit(f"Unexpected AEH numeric-range warning in {target}; review the pinned source")
    target.write_text(source.replace(ORIGINAL, PATCHED, 1))


if __name__ == "__main__":
    main()
