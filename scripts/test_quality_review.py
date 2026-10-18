"""Independent AI quality review of a skill submission.

Reviews the coherence between skill, instruction, and tests regardless of
whether the submission was manually authored or AI-generated.  Produces a
structured JSON assessment with pass/warn/fail recommendation.

This review is ADVISORY (non-blocking) - always exits 0 regardless of result.
The assessment is logged for informational purposes but does not fail the pipeline.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

from abevalflow import llm_client
from abevalflow.schemas import SubmissionMetadata

logger = logging.getLogger(__name__)

REVIEW_SYSTEM_PROMPT_AEH = """\
You are a senior QA engineer reviewing Agent-Eval-Harness (AEH) submissions.
You must assess the quality of a submission that consists of:
- metadata.yaml (pipeline/experiment config)
- eval.yaml (single mode) and/or eval-control.yaml + eval-treatment.yaml (pairwise)
- cases/ with input.yaml (and optional annotations.yaml)
- Optionally skills/**/SKILL.md when plugin_dirs are used

Evaluate the following dimensions and assign each a score from 0.0 to 1.0:

1. **coherence** — Do the eval configs and cases faithfully test the intended skill/behavior?
2. **coverage** — Do the cases and judges adequately verify expected outcomes?
3. **clarity** — Are prompts, judges, and thresholds clear and unambiguous?
4. **feasibility** — Can an AI agent reasonably complete the cases?
5. **robustness** — Do cases/judges cover edge cases and failure modes?
6. **specificity** — Are case prompts concrete enough?
7. **completeness** — Are required AEH fields present (dataset, judges, models, outputs)?

For each dimension, provide a brief finding (1-2 sentences).

Then provide an overall recommendation:
- "pass" — Submission is ready for evaluation (all scores >= 0.6)
- "warn" — Minor issues but can proceed (some scores 0.4-0.6)
- "fail" — Significant problems, should not proceed (any score < 0.4)

Output ONLY valid JSON with this structure:
{
  "dimensions": {
    "coherence": {"score": 0.0, "finding": "..."},
    "coverage": {"score": 0.0, "finding": "..."},
    "clarity": {"score": 0.0, "finding": "..."},
    "feasibility": {"score": 0.0, "finding": "..."},
    "robustness": {"score": 0.0, "finding": "..."},
    "specificity": {"score": 0.0, "finding": "..."},
    "completeness": {"score": 0.0, "finding": "..."}
  },
  "overall_score": 0.0,
  "recommendation": "pass|warn|fail",
  "summary": "One paragraph overall assessment"
}
"""

REVIEW_USER_TEMPLATE_AEH = """\
Review the following AEH submission.

## Skill Name
{skill_name}

## metadata.yaml
```yaml
{metadata_content}
```

## Eval configs
{eval_configs_section}

## Sample cases
{cases_section}

## Optional SKILL.md
```
{skill_content}
```

Respond with the JSON assessment.
"""


def _read_file_safe(path: Path) -> str:
    if path.is_file():
        return path.read_text()
    return "(file not present)"


def _find_skill_md(submission_dir: Path) -> Path | None:
    """Find SKILL.md in various locations (newer and older formats)."""
    # Newer format: skills/SKILL.md
    direct = submission_dir / "skills" / "SKILL.md"
    if direct.is_file():
        return direct
    # Nested / AEH format: skills/<skill-name>/SKILL.md
    skills_dir = submission_dir / "skills"
    if skills_dir.is_dir():
        for subdir in skills_dir.iterdir():
            if subdir.is_dir():
                nested = subdir / "SKILL.md"
                if nested.is_file():
                    return nested
    # Any SKILL.md under submission (fallback)
    matches = sorted(submission_dir.rglob("SKILL.md"))
    return matches[0] if matches else None


def _aeh_eval_configs_section(submission_dir: Path) -> str:
    parts: list[str] = []
    for name in ("eval.yaml", "eval-control.yaml", "eval-treatment.yaml"):
        path = submission_dir / name
        if path.is_file():
            parts.append(f"### {name}\n```yaml\n{path.read_text()}\n```")
    return "\n\n".join(parts) if parts else "(no eval.yaml / eval-*.yaml present)"


def _aeh_cases_section(submission_dir: Path, max_cases: int = 3) -> str:
    cases_dir = submission_dir / "cases"
    if not cases_dir.is_dir():
        return "(cases/ not present)"
    case_dirs = sorted(d for d in cases_dir.iterdir() if d.is_dir())[:max_cases]
    if not case_dirs:
        return "(cases/ is empty)"
    parts: list[str] = []
    for case_dir in case_dirs:
        input_path = case_dir / "input.yaml"
        ann_path = case_dir / "annotations.yaml"
        block = f"### {case_dir.name}\n"
        block += f"**input.yaml**\n```yaml\n{_read_file_safe(input_path)}\n```\n"
        if ann_path.is_file():
            block += f"**annotations.yaml**\n```yaml\n{ann_path.read_text()}\n```\n"
        parts.append(block)
    return "\n".join(parts)


def _advisory_aeh_missing_files(submission_dir: Path, skill_name: str) -> dict | None:
    """Return a warn/pass assessment when required AEH files are missing."""
    has_eval = any(
        (submission_dir / name).is_file() for name in ("eval.yaml", "eval-control.yaml", "eval-treatment.yaml")
    )
    has_meta = (submission_dir / "metadata.yaml").is_file()
    has_cases = (submission_dir / "cases").is_dir() and any((submission_dir / "cases").iterdir())
    if has_eval and has_meta and has_cases:
        return None
    missing = []
    if not has_meta:
        missing.append("metadata.yaml")
    if not has_eval:
        missing.append("eval.yaml or eval-control/treatment.yaml")
    if not has_cases:
        missing.append("cases/")
    return {
        "dimensions": {},
        "overall_score": 0.5,
        "recommendation": "warn",
        "summary": (
            f"AEH submission '{skill_name}' is missing required files for a full "
            f"quality review: {', '.join(missing)}. Proceeding with advisory warn."
        ),
        "passed": True,
        "engine": "aeh",
    }


def _normalize_assessment(assessment: dict, *, engine: str | None = None) -> dict:
    if "overall_score" not in assessment:
        dims = assessment.get("dimensions", {})
        scores = [d.get("score", 0.0) for d in dims.values() if isinstance(d, dict)]
        assessment["overall_score"] = sum(scores) / len(scores) if scores else 0.0

    if "recommendation" not in assessment:
        score = assessment["overall_score"]
        if score >= 0.6:
            assessment["recommendation"] = "pass"
        elif score >= 0.4:
            assessment["recommendation"] = "warn"
        else:
            assessment["recommendation"] = "fail"

    # AEH reviews are advisory for the test phase; keep passed=true by default.
    if engine == "aeh":
        assessment["passed"] = True
        assessment["engine"] = "aeh"
    else:
        assessment["passed"] = assessment["recommendation"] != "fail"
    return assessment


def review_submission(submission_dir: Path) -> dict:
    """Run AI quality review and return the structured assessment."""
    meta_path = submission_dir / "metadata.yaml"
    with meta_path.open() as f:
        raw = yaml.safe_load(f)
    metadata = SubmissionMetadata(**raw)

    skill_md_path = _find_skill_md(submission_dir)
    skill_content = _read_file_safe(skill_md_path) if skill_md_path else "(file not present)"

    advisory = _advisory_aeh_missing_files(submission_dir, metadata.name)
    if advisory is not None:
        return advisory
    system_prompt = REVIEW_SYSTEM_PROMPT_AEH
    user_prompt = REVIEW_USER_TEMPLATE_AEH.format(
        skill_name=metadata.name,
        metadata_content=_read_file_safe(meta_path),
        eval_configs_section=_aeh_eval_configs_section(submission_dir),
        cases_section=_aeh_cases_section(submission_dir),
        skill_content=skill_content,
    )
    engine = "aeh"

    llm_result = llm_client.chat_completion_with_usage(
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=4096,
    )
    response_text = llm_result.content

    try:
        assessment = json.loads(response_text)
    except json.JSONDecodeError:
        import re

        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if json_match:
            assessment = json.loads(json_match.group())
        else:
            raise ValueError("LLM review response is not valid JSON")

    assessment = _normalize_assessment(assessment, engine=engine)
    assessment["token_usage"] = {
        "prompt_tokens": llm_result.prompt_tokens,
        "completion_tokens": llm_result.completion_tokens,
        "total_tokens": llm_result.total_tokens,
        "model": llm_result.model,
    }

    return assessment


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="AI quality review of submission")
    parser.add_argument("submission_dir", type=Path, help="Path to the submission directory")
    args = parser.parse_args(argv)

    if not args.submission_dir.is_dir():
        result = {"passed": False, "error": f"Not a directory: {args.submission_dir}"}
        print(json.dumps(result, indent=2))
        return 1

    try:
        assessment = review_submission(args.submission_dir)
    except Exception as exc:
        logger.error("Review failed: %s", exc)
        # Prefer non-blocking for AEH even on unexpected errors
        is_aeh = (args.submission_dir / "eval.yaml").is_file() or (
            args.submission_dir / "eval-treatment.yaml"
        ).is_file()
        result = {
            "passed": True if is_aeh else False,
            "recommendation": "warn" if is_aeh else "fail",
            "error": str(exc),
            "engine": "aeh" if is_aeh else None,
        }
        print(json.dumps(result, indent=2))
        return 0 if is_aeh else 1

    print(json.dumps(assessment, indent=2))
    # Always return 0 - this review is advisory (non-blocking)
    # The assessment result is logged but doesn't fail the pipeline
    if not assessment.get("passed", False):
        logger.warning(
            "Quality review recommendation: %s (advisory, non-blocking)", assessment.get("recommendation", "unknown")
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
