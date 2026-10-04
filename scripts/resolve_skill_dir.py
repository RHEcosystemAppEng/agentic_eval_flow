#!/usr/bin/env python3
"""Resolve an AEH submission's real skill directory from eval.yaml.

AEH submissions reference their skill via eval.yaml's runner.plugin_dirs
instead of embedding SKILL.md in the submission dir itself (e.g.
plugin_dirs: ["../../../rh-sre"], skill: execution-summary ->
rh-sre/skills/execution-summary/SKILL.md). This resolves that indirection
so downstream tools (e.g. the SKILL.md security scanner) have a real
directory containing SKILL.md to point at, instead of the bare submission
directory.

Usage::

    python scripts/resolve_skill_dir.py /path/to/submission
    # prints the resolved directory to stdout and exits 0, or
    # prints nothing and exits 1 if no candidate has a SKILL.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


def resolve_skill_dir(submission_dir: Path) -> Path | None:
    """Return the real skill directory (containing SKILL.md), or None."""
    eval_yaml = submission_dir / "eval.yaml"
    if not eval_yaml.is_file():
        return None

    data = yaml.safe_load(eval_yaml.read_text()) or {}
    skill_name = data.get("skill") or (data.get("execution") or {}).get("skill")
    plugin_dirs = (data.get("runner") or {}).get("plugin_dirs") or []

    for rel in plugin_dirs:
        root = (submission_dir / str(rel)).resolve()
        candidates = []
        if skill_name:
            candidates.append(root / str(skill_name))
            candidates.append(root / "skills" / str(skill_name))
        candidates.append(root)
        for cand in candidates:
            if (cand / "SKILL.md").is_file():
                return cand
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("submission_dir", type=Path, help="AEH submission directory to resolve")
    args = parser.parse_args(argv)

    resolved = resolve_skill_dir(args.submission_dir)
    if resolved is None:
        return 1
    print(resolved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
