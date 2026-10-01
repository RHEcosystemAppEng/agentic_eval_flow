#!/usr/bin/env python3
"""MCP Phase 1 secrets scan.

Runs gitleaks over an MCP server repository (static working-tree scan, no live
verification) and normalizes its output into secrets-scan.json for SecretsGate.

Usage:
    python -m scripts.mcp.secrets_scan <target_dir> --reports-dir <dir>
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

from scripts.mcp._common import make_finding, run_tool, write_scan

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCAN_FILENAME = "secrets-scan.json"
SCANNER = "gitleaks"

# gitleaks has no severity field; every hit is a candidate hardcoded credential,
# so all findings are HIGH (the gate blocks on HIGH/CRITICAL in block mode).


def normalize(gitleaks_findings: list[dict]) -> list[dict]:
    """Map gitleaks JSON records to normalized findings."""
    findings: list[dict] = []
    for rec in gitleaks_findings:
        rule_id = rec.get("RuleID", "unknown")
        findings.append(
            make_finding(
                severity="high",
                message=rec.get("Description", "Hardcoded secret detected"),
                rule_id=rule_id,
                file_path=rec.get("File"),
                line=rec.get("StartLine"),
            )
        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP Phase 1 secrets scan (gitleaks)")
    parser.add_argument("target_dir", type=Path, help="MCP server repo to scan")
    parser.add_argument(
        "--reports-dir",
        type=Path,
        required=True,
        help="Directory to write secrets-scan.json into",
    )
    args = parser.parse_args()

    if not args.target_dir.is_dir():
        logger.error("Not a directory: %s", args.target_dir)
        return 1

    scan_path = args.reports_dir / SCAN_FILENAME

    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tmp:
        report_path = Path(tmp.name)

    # gitleaks `dir` scans the working tree (no git history required).
    result = run_tool(
        [
            "gitleaks",
            "dir",
            str(args.target_dir),
            "--report-format",
            "json",
            "--report-path",
            str(report_path),
            "--no-banner",
            "--exit-code",
            "0",  # never fail the process on findings; the gate decides pass/fail
        ]
    )

    # Exit codes: 0 clean/normal. Any output on a crash goes to stderr.
    if result.returncode != 0:
        logger.error("gitleaks failed (exit %d): %s", result.returncode, result.stderr.strip())
        return 1

    try:
        raw = json.loads(report_path.read_text() or "[]")
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Could not read gitleaks report: %s", exc)
        return 1
    finally:
        report_path.unlink(missing_ok=True)

    findings = normalize(raw if isinstance(raw, list) else [])
    write_scan(scan_path, SCANNER, args.target_dir, findings)
    logger.info("Secrets scan complete: %d finding(s)", len(findings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
