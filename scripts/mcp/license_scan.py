#!/usr/bin/env python3
"""MCP Phase 1 license-compliance scan.

Runs licensee to detect the repository's TOP-LEVEL declared license (from the
LICENSE file and/or package manifests) and checks it against an approved-license
allow-list. Emits license-scan.json for LicenseGate. Transitive/dependency
license scanning is out of scope (ADR Phase 1).

Usage:
    python -m scripts.mcp.license_scan <target_dir> --reports-dir <dir> \
        [--allow apache-2.0 --allow mit ...]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from abevalflow.mcp.phase1.license import DEFAULT_ALLOWED_LICENSES
from scripts.mcp._common import make_finding, run_tool, write_scan

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SCAN_FILENAME = "license-scan.json"
SCANNER = "licensee"


def evaluate_licenses(
    detected: list[dict],
    allowed: set[str],
    matched_files: list[dict],
) -> list[dict]:
    """Produce findings for a missing or disallowed top-level license.

    A repo passes when at least one detected license is in the allow-list.
    licensee emits canonical SPDX ids (e.g. "Apache-2.0"); compare
    case-insensitively by lowercasing both sides.
    """
    location = matched_files[0].get("filename") if matched_files else None

    spdx_ids = [
        str(lic.get("spdx_id", "")).lower()
        for lic in detected
        if lic.get("spdx_id") and str(lic.get("spdx_id")).lower() != "noassertion"
    ]

    if not spdx_ids:
        return [
            make_finding(
                severity="high",
                message="No top-level declared license could be identified.",
                rule_id="license-missing",
                file_path=location,
            )
        ]

    if any(spdx in allowed for spdx in spdx_ids):
        return []

    return [
        make_finding(
            severity="high",
            message=(f"Declared license(s) {', '.join(spdx_ids)} not in allow-list ({', '.join(sorted(allowed))})."),
            rule_id="license-not-allowed",
            file_path=location,
            detected=spdx_ids,
        )
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP Phase 1 license scan (licensee)")
    parser.add_argument("target_dir", type=Path, help="MCP server repo to scan")
    parser.add_argument(
        "--reports-dir",
        type=Path,
        required=True,
        help="Directory to write license-scan.json into",
    )
    parser.add_argument(
        "--allow",
        action="append",
        default=None,
        metavar="SPDX_ID",
        help="Approved SPDX id (repeatable). Defaults to DEFAULT_ALLOWED_LICENSES.",
    )
    args = parser.parse_args()

    if not args.target_dir.is_dir():
        logger.error("Not a directory: %s", args.target_dir)
        return 1

    allowed = {a.lower() for a in (args.allow or DEFAULT_ALLOWED_LICENSES)}
    scan_path = args.reports_dir / SCAN_FILENAME

    result = run_tool(["licensee", "detect", "--json", str(args.target_dir)])
    if result.returncode != 0:
        logger.error("licensee failed (exit %d): %s", result.returncode, result.stderr.strip())
        return 1

    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        logger.error("Could not parse licensee JSON: %s", exc)
        return 1

    findings = evaluate_licenses(
        data.get("licenses", []),
        allowed,
        data.get("matched_files", []),
    )
    write_scan(scan_path, SCANNER, args.target_dir, findings)
    logger.info("License scan complete: %d finding(s)", len(findings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
