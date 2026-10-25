#!/usr/bin/env python3
"""Validate one skill directory and save its certification check as JSON."""

from __future__ import annotations

import argparse
from pathlib import Path

from abevalflow.skill_structure import validate_skill_structure


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_dir", type=Path, help="Directory containing SKILL.md")
    parser.add_argument("--output", type=Path, required=True, help="Skill structure validation report")
    args = parser.parse_args(argv)

    result = validate_skill_structure(args.skill_dir)
    payload = result.model_dump_json(indent=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload + "\n")
    print(payload)
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
