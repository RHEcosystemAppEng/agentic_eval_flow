"""Command interface for analyze."""

from __future__ import annotations

import argparse
from pathlib import Path

from abevalflow.report import AnalysisResult
from abevalflow.reporting.markdown import render_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-json", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = AnalysisResult.model_validate_json(args.report_json.read_text())
    output = args.output or args.report_json.with_suffix(".md")
    output.write_text(render_markdown(result))
    return 0
