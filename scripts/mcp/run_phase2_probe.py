#!/usr/bin/env python3
"""MCP Phase 2 live conformance probe — CLI entry point.

Connects to a running MCP server over Streamable HTTP and runs the deterministic
(no-LLM) contract/conformance checks, writing one three-state result file per
check for the Phase 2 gates to read.

Check logic lives in ``abevalflow.mcp.phase2.checks``; this module is the thin
CLI wrapper that creates the client, invokes the checks, and writes results.

Checks that Compass already collects as real automated facts (tool-name rules,
OAuth match) are NOT probed here — they are consumed via
``scripts/mcp/run_compass_fetch.py`` instead.

Usage:
    python -m scripts.mcp.run_phase2_probe --server-url http://<service>:8080/mcp \
        --reports-dir <dir> [--timeout 30] [--openapi-url URL]
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from abevalflow.mcp.phase2.checks import probe_all
from abevalflow.mcp.types import write_check_result

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP Phase 2 live conformance probe")
    parser.add_argument("--server-url", required=True, help="MCP endpoint URL, e.g. http://<service>:8080/mcp")
    parser.add_argument(
        "--reports-dir", type=Path, required=True, help="Directory to write <check>-check.json files into"
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="Per-request HTTP timeout in seconds")
    parser.add_argument(
        "--openapi-url",
        default=None,
        help="Explicit OpenAPI contract URL. If omitted, well-known paths on the server root are probed.",
    )
    args = parser.parse_args()

    start = time.monotonic()
    outcomes = probe_all(args.server_url, timeout=args.timeout, openapi_url=args.openapi_url)
    for outcome in outcomes:
        write_check_result(args.reports_dir, outcome)
    logger.info("Phase 2 probe complete: %d checks in %.1fs", len(outcomes), time.monotonic() - start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
