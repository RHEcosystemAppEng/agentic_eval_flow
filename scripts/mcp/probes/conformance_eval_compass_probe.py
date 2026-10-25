#!/usr/bin/env python3
"""MCP conformance evaluation Compass-consumed checks — CLI entry point.

Two modes:

- **File mode** (``--facts-file``): reads raw Compass facts from a local JSON
  file and maps subfields to check outcomes. Used for offline/demo runs.
- **Live mode** (``--compass-url`` + ``--entity-ref``): fetches check results
  from the SoundCheck REST API and maps states to check outcomes. Used when the
  pipeline has access to Compass staging/prod.

If both are given, live mode takes precedence. If neither is given the consumed
checks report not_evaluated.

Mapping logic lives in ``abevalflow.mcp.checks.conformance_eval_compass_checks``; this module is the
thin CLI wrapper that handles I/O (file reads, HTTP calls) and writes results.

Usage (file mode):
    python -m scripts.mcp.probes.conformance_eval_compass_probe \\
        --facts-file facts.json --reports-dir <dir>

Usage (live mode):
    python -m scripts.mcp.probes.conformance_eval_compass_probe \\
        --compass-url https://compass.stage.redhat.com/api/soundcheck \\
        --entity-ref mcpserver:redhat/my-server \\
        --reports-dir <dir>

Environment:
    COMPASS_API_KEY — Bearer token for the SoundCheck API (live mode only).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from abevalflow.mcp.checks.conformance_eval_compass_checks import (
    CHECK_OAUTH,
    CHECK_TOOL_NAME,
    map_facts,
    map_results,
)
from abevalflow.mcp.helpers import write_check_result

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _fetch_results(compass_url: str, entity_ref: str, api_key: str) -> list[dict]:
    """GET check results from the SoundCheck REST API.

    Returns the ``results`` array, or an empty list on any failure (the caller
    degrades to not_evaluated).
    """
    encoded_ref = quote(entity_ref, safe="")
    url = f"{compass_url.rstrip('/')}/results?entityRef={encoded_ref}&checks={CHECK_TOOL_NAME}&checks={CHECK_OAUTH}"
    logger.info("Fetching Compass results: %s", url)
    # Red Hat internal Compass uses a corporate CA. Honor SSL_CERT_FILE / REQUESTS_CA_BUNDLE
    # so the image or the developer can point to the RH root CA without code changes.
    ca_bundle = os.environ.get("SSL_CERT_FILE") or os.environ.get("REQUESTS_CA_BUNDLE") or True
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=30.0,
            verify=ca_bundle,
        )
    except (httpx.HTTPError, OSError) as exc:
        # OSError covers FileNotFoundError from a stale SSL_CERT_FILE path.
        logger.warning("Compass API request failed: %s", exc)
        return []
    if resp.status_code != 200:
        logger.warning("Compass API returned HTTP %d: %s", resp.status_code, resp.text[:200])
        return []
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Compass API response not parseable: %s", exc)
        return []
    results = data if isinstance(data, list) else data.get("results", [])
    logger.info("Compass returned %d result(s) for %s", len(results), entity_ref)
    return results


def _load_facts_file(path: Path) -> dict[str, Any]:
    """Read a local Compass facts JSON file, returning {} on any failure."""
    if not path.is_file():
        logger.warning("Facts file not found: %s; consumed checks reported not_evaluated.", path)
        return {}
    try:
        loaded = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read facts file %s (%s); consumed checks reported not_evaluated.", path, exc)
        return {}
    if not isinstance(loaded, dict):
        logger.warning("Facts file %s is not a JSON object; consumed checks reported not_evaluated.", path)
        return {}
    return loaded


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP conformance evaluation Compass-consumed checks")
    parser.add_argument(
        "--reports-dir", type=Path, required=True, help="Directory to write <check>-check.json files into"
    )
    # File mode
    parser.add_argument("--facts-file", type=Path, default=None, help="Local JSON file of Compass facts (file mode)")
    # Live mode
    parser.add_argument(
        "--compass-url",
        default=None,
        help="SoundCheck API base URL, e.g. https://compass.stage.redhat.com/api/soundcheck (live mode)",
    )
    parser.add_argument(
        "--entity-ref",
        default=None,
        help="Compass entity ref, e.g. mcpserver:redhat/my-server (live mode)",
    )
    args = parser.parse_args()

    # Live mode takes precedence over file mode.
    if args.compass_url and args.entity_ref:
        api_key = os.environ.get("COMPASS_API_KEY", "")
        if not api_key:
            logger.warning("COMPASS_API_KEY not set; cannot fetch live results. Falling back to not_evaluated.")
            outcomes = map_results([])
        else:
            results = _fetch_results(args.compass_url, args.entity_ref, api_key)
            outcomes = map_results(results)
    elif args.facts_file:
        facts = _load_facts_file(args.facts_file)
        outcomes = map_facts(facts)
    else:
        logger.warning("No --facts-file or --compass-url/--entity-ref given; consumed checks reported not_evaluated.")
        outcomes = map_facts({})

    for outcome in outcomes:
        write_check_result(args.reports_dir, outcome)
    logger.info("Compass consume complete: %d checks", len(outcomes))
    return 0


if __name__ == "__main__":
    sys.exit(main())
