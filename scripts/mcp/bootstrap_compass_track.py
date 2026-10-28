#!/usr/bin/env python3
"""Bootstrap the dedicated MCP evaluation SoundCheck track on Compass.

Registers owned check definitions via ``POST /api/soundcheck/checks`` and
creates or updates the dedicated track via ``POST``/``PUT /api/soundcheck/tracks``. Two Compass-
owned MCP checks are referenced by ID and are never recreated.
Run manually once per Compass environment (staging, prod).  NOT part of
the pipeline — this is a one-time setup step.

**Dry-run is ON by default.**  Use ``--no-dry-run`` to actually register.

Usage:
    python -m scripts.mcp.bootstrap_compass_track \\
        --compass-url https://compass.stage.redhat.com/api/soundcheck \\
        [--no-dry-run]

Environment:
    COMPASS_API_KEY — Bearer token for the SoundCheck API.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from typing import Any

from abevalflow.mcp.phase_config import PHASES
from abevalflow.mcp.checks.compass_publish import (
    PHASE_FACT_MAPS,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

OWNER = "group:redhat/ai5-marketplace"
FILTER = [{"kind": ["mcpserver"]}]
TRACK_ID = "abevalflow-mcp-evaluation"
TRACK_NAME = "ABEvalFlow MCP Evaluation"

# ---------------------------------------------------------------------------
# Check definitions
# ---------------------------------------------------------------------------


def _check_def(
    check_id: str,
    name: str,
    description: str,
    fact_ref: str,
    path: str = "$.passed",
    operator: str = "equal",
    value: Any = True,
    passed_message: str = "",
    failed_message: str = "",
) -> dict[str, Any]:
    return {
        "id": check_id,
        "name": name,
        "description": description,
        "ownerEntityRef": OWNER,
        "pathResolver": "jsonpath",
        "filter": FILTER,
        "rule": {
            "all": [
                {
                    "factRef": fact_ref,
                    "path": path,
                    "operator": operator,
                    "value": value,
                }
            ]
        },
        "passedMessage": passed_message or f"{name} passed.",
        "failedMessage": failed_message or f"{name} failed.",
    }


# Track metadata and order come from the same phase definitions as the publisher.
PHASE_CHECK_METADATA: dict[str, dict[str, tuple[str, str]]] = {
    phase_name: {
        check_id: (check["track_name"], check["description"])
        for check_id, check in phase["checks"].items()
        if check.get("fact_ref") and check.get("track_name") and check.get("description")
    }
    for phase_name, phase in PHASES.items()
}


def _build_checks_for_phase(phase_name: str) -> list[dict[str, Any]]:
    """Build check definitions for a phase from metadata and fact maps."""
    fact_map = PHASE_FACT_MAPS.get(phase_name, {})
    metadata = PHASE_CHECK_METADATA.get(phase_name, {})
    checks = []
    for check_id, (check_name, description) in metadata.items():
        fact_ref = fact_map.get(check_id)
        if fact_ref is None:
            logger.warning("No fact mapping for %s in phase %s; skipping check", check_id, phase_name)
            continue
        check_key = f"mcp-eval-{check_id.replace('_', '-')}" if "_" in check_id else f"mcp-eval-{check_id}"
        checks.append(_check_def(check_key, check_name, description, fact_ref))
    return checks


PHASE_CHECKS: dict[str, list[dict[str, Any]]] = {
    phase_name: _build_checks_for_phase(phase_name) for phase_name in PHASES
}
ALL_CHECK_DEFS = [check for checks in PHASE_CHECKS.values() for check in checks]
CONSUMED_TRACK_CHECKS = [
    check
    for phase in PHASES.values()
    for check in phase.get("consumed_track_checks", [])
]
OWNED_TRACK_CHECKS = [{key: check[key] for key in ("id", "name", "description")} for check in ALL_CHECK_DEFS]
PHASE_LEVEL_METADATA: dict[str, tuple[str, str]] = {
    name: (phase.get("display_name", name), phase.get("description", ""))
    for name, phase in PHASES.items()
}


def _build_levels() -> list[dict[str, Any]]:
    """Build track levels in registry order with the checks of THAT phase."""
    levels = []
    for ordinal, (phase_name, phase) in enumerate(PHASES.items(), 1):
        level_checks = [
            {key: check[key] for key in ("id", "name", "description")}
            for check in PHASE_CHECKS[phase_name]
        ]
        level_checks.extend(phase.get("consumed_track_checks", []))
        level_name, level_description = PHASE_LEVEL_METADATA[phase_name]
        levels.append({
            "ordinal": ordinal,
            "name": level_name,
            "description": level_description,
            "checks": level_checks,
        })
    return levels


TRACK_DEFINITION = {
    "id": TRACK_ID,
    "type": "standard",
    "name": TRACK_NAME,
    "description": (
        "Static, conformance, and behavioral MCP evaluation results from ABEvalFlow. "
        "Each phase is shown as a separate level."
    ),
    "ownerEntityRef": OWNER,
    "filter": FILTER,
    "levels": _build_levels(),
}


def _register_check(
    compass_url: str,
    check_def: dict[str, Any],
    api_key: str,
    dry_run: bool,
) -> bool:
    check_id = check_def["id"]
    url = f"{compass_url.rstrip('/')}/checks/{check_id}"

    if dry_run:
        logger.info("[DRY RUN] Would PUT %s:\n%s", url, json.dumps(check_def, indent=2))
        return True

    data = json.dumps(check_def).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    request = urllib.request.Request(url, data=data, headers=headers, method="PUT")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            logger.info("Registered %s (HTTP %d)", check_id, response.status)
            return True
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        logger.error("Failed to register %s: HTTP %d %s: %s", check_id, exc.code, exc.reason, detail)
        return False
    except urllib.error.URLError as exc:
        logger.error("Failed to register %s: %s", check_id, exc.reason)
        return False


def _register_checks(
    compass_url: str,
    check_defs: list[dict[str, Any]],
    api_key: str,
    dry_run: bool,
) -> bool:
    """Register checks with the bulk POST API, falling back to per-check PUT on conflict."""
    url = f"{compass_url.rstrip('/')}/checks"
    payload = {"checks": check_defs}

    if dry_run:
        logger.info(
            "[DRY RUN] Would POST %s with %d check definitions",
            url,
            len(check_defs),
        )
        return True

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            logger.info("Registered %d checks (HTTP %d)", len(check_defs), response.status)
            return True
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code == 409:
            logger.warning("Bulk check POST found existing IDs; falling back to individual PUTs")
            return all(_register_check(compass_url, check_def, api_key, dry_run=False) for check_def in check_defs)
        logger.error("Failed to register checks: HTTP %d %s: %s", exc.code, exc.reason, detail)
        return False
    except urllib.error.URLError as exc:
        logger.error("Failed to register checks: %s", exc.reason)
        return False


def _register_track(compass_url: str, api_key: str, dry_run: bool) -> bool:
    url = f"{compass_url.rstrip('/')}/tracks"
    payload = {"tracks": [TRACK_DEFINITION]}

    if dry_run:
        logger.info("[DRY RUN] Would POST %s:\n%s", url, json.dumps(payload, indent=2))
        return True

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            logger.info("Created track %s (HTTP %d)", TRACK_ID, response.status)
            return True
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if exc.code in (409, 400):
            logger.info("Track %s already exists; updating its levels", TRACK_ID)
            update = urllib.request.Request(
                f"{url}/{TRACK_ID}",
                data=json.dumps(TRACK_DEFINITION).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="PUT",
            )
            try:
                with urllib.request.urlopen(update, timeout=30) as response:
                    logger.info("Updated track %s (HTTP %d)", TRACK_ID, response.status)
                    return True
            except urllib.error.HTTPError as update_exc:
                update_detail = update_exc.read().decode("utf-8", errors="replace")
                logger.error(
                    "Failed to update track %s: HTTP %d %s: %s",
                    TRACK_ID,
                    update_exc.code,
                    update_exc.reason,
                    update_detail,
                )
                return False
        logger.error("Failed to create track %s: HTTP %d %s: %s", TRACK_ID, exc.code, exc.reason, detail)
        return False
    except urllib.error.URLError as exc:
        logger.error("Failed to create track %s: %s", TRACK_ID, exc.reason)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap MCP evaluation SoundCheck track on Compass")
    parser.add_argument(
        "--compass-url",
        required=True,
        help="SoundCheck API base URL (e.g. https://compass.stage.redhat.com/api/soundcheck)",
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_true",
        default=False,
        help="Actually register checks on Compass (default is dry-run)",
    )
    args = parser.parse_args()

    dry_run = not args.no_dry_run
    api_key = os.environ.get("COMPASS_API_KEY", "")

    if not dry_run and not api_key:
        logger.error("COMPASS_API_KEY not set; cannot register checks.")
        return 1

    if dry_run:
        logger.info("DRY RUN — check definitions will be logged but not registered")

    logger.info("Registering %d MCP evaluation checks on %s", len(ALL_CHECK_DEFS), args.compass_url)
    checks_ok = _register_checks(args.compass_url, ALL_CHECK_DEFS, api_key, dry_run)
    if not checks_ok and not dry_run:
        logger.warning("Check registration failed; attempting track update anyway")

    if not _register_track(args.compass_url, api_key, dry_run):
        return 1

    if checks_ok:
        logger.info(
            "All %d checks and track %s %s",
            len(ALL_CHECK_DEFS),
            TRACK_ID,
            "registered" if not dry_run else "would be registered",
        )
    else:
        logger.info(
            "Track %s %s (checks already exist)",
            TRACK_ID,
            "updated" if not dry_run else "would be updated",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
