#!/usr/bin/env python3
"""MCP Phase 1 "does not execute user-provided code" scan.

Runs semgrep with the bundled offline ruleset (rules/no_user_code.yml) over an
MCP server repository and normalizes results into no-user-code-scan.json for
NoUserCodeGate.

Usage:
    python -m scripts.mcp.no_user_code_scan <target_dir> --reports-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from scripts.mcp._common import make_finding, run_tool, write_scan

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCAN_FILENAME = "no-user-code-scan.json"
SCANNER = "semgrep"
RULES_PATH = Path(__file__).parent / "rules" / "no_user_code.yml"

# semgrep severity -> our severity.
_SEVERITY_MAP = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}


def normalize(semgrep_results: list[dict]) -> list[dict]:
    """Map semgrep JSON results to normalized findings."""
    findings: list[dict] = []
    for res in semgrep_results:
        extra = res.get("extra", {})
        sev = _SEVERITY_MAP.get(str(extra.get("severity", "")).upper(), "medium")
        findings.append(
            make_finding(
                severity=sev,
                message=extra.get("message", "Dynamic/arbitrary code execution pattern"),
                rule_id=res.get("check_id", "unknown"),
                file_path=res.get("path"),
                line=res.get("start", {}).get("line"),
            )
        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP Phase 1 no-user-code-execution scan (semgrep)")
    parser.add_argument("target_dir", type=Path, help="MCP server repo to scan")
    parser.add_argument(
        "--reports-dir",
        type=Path,
        required=True,
        help="Directory to write no-user-code-scan.json into",
    )
    args = parser.parse_args()

    if not args.target_dir.is_dir():
        logger.error("Not a directory: %s", args.target_dir)
        return 1
    if not RULES_PATH.is_file():
        logger.error("Ruleset missing: %s", RULES_PATH)
        return 1

    scan_path = args.reports_dir / SCAN_FILENAME

    result = run_tool(
        [
            "semgrep",
            "scan",
            "--config",
            str(RULES_PATH),
            "--json",
            "--quiet",
            "--metrics=off",  # stay fully offline (no telemetry call)
            "--no-git-ignore",  # scan every file in the artifact, not just tracked
            str(args.target_dir),
        ]
    )

    # semgrep exits 0 on success (with or without findings); non-zero = tool error.
    if result.returncode != 0:
        logger.error("semgrep failed (exit %d): %s", result.returncode, result.stderr.strip())
        return 1

    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        logger.error("Could not parse semgrep JSON: %s", exc)
        return 1

    findings = normalize(data.get("results", []))
    write_scan(scan_path, SCANNER, args.target_dir, findings)
    logger.info("No-user-code scan complete: %d finding(s)", len(findings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
