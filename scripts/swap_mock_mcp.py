#!/usr/bin/env python3
"""Swap mock MCP server entries into a submission's committed mcps.json pack(s).

Extracted from konflux-shared-tasks/task/aeh-skill-eval/aeh-skill-eval.yaml's
``swap-mock-mcp`` step (previously an untested inline Python heredoc) so this
logic has real test coverage (tests/test_swap_mock_mcp.py) and runs
identically from both the Tekton pipeline and local ``run_aeh.py`` dev runs --
no separate "remember to run the pipeline step" footgun for local testing.

Two things this does, both file-level and idempotent, mutating an ephemeral
checkout in place (never committed back -- see ``mcps.real.json`` backups):

1. REAL pack swap (original behavior, unchanged output): for every pack root
   referenced by ``runner.plugin_dirs[0]`` across this submission's
   ``eval.yaml`` / ``eval-control.yaml`` / ``eval-treatment.yaml`` (whichever
   exist) that already commits a real ``mcps.json``, rewrite any server entry
   name also found under ``cases/*/mock-mcps/<name>/fixture.json`` to a stdio
   command launching the shared mock-mcp-binary with ``--schema``/
   ``--fixtures`` pointing at the workspace-relative paths
   ``abevalflow.harbor_extensions.aeh_task_enrichment`` stages per task.

2. MCP-ONLY sibling generation (new): a pairwise control arm's
   ``eval-control.yaml`` may declare ``runner.plugin_dirs`` pointing at a
   sibling pack named ``"<real-pack-name>-mcp-only"`` that commits NO
   ``SKILL.md`` and usually doesn't exist in git at all. That's the ONLY
   mechanism that actually prevents ``agent_eval/harbor/run.py``'s
   ``_resolve_harbor_skill_roots`` from forwarding ``--skill`` for a
   plugin_dirs entry (a per-directory SKILL.md check, with no flag-based
   override -- see eval-control.yaml's own comment for the empirical proof).
   This function auto-creates that sibling directory, copies the real
   pack's own (pre-swap) ``mcps.json`` into it verbatim, then applies the
   exact same swap as (1) -- so its content is always freshly derived from
   the real pack and never hand-authored or allowed to drift.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_MCP_ONLY_SUFFIX = "-mcp-only"
_CONFIG_FILENAMES = ("eval.yaml", "eval-control.yaml", "eval-treatment.yaml")
DEFAULT_MOCK_BINARY = "/opt/mock-mcp/src/server.py"


def _load_yaml_dict(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _mock_server_names(cases_root: Path) -> set[str]:
    """Union of ``mock-mcps/<server>/`` names across every case -- mirrors
    abevalflow.harbor_extensions.aeh_task_enrichment._mock_mcp_server_names,
    which must agree with this on which server names get mocked."""
    names: set[str] = set()
    if not cases_root.is_dir():
        return names
    for case_dir in cases_root.iterdir():
        mock_mcps_dir = case_dir / "mock-mcps"
        if not mock_mcps_dir.is_dir():
            continue
        for server_dir in mock_mcps_dir.iterdir():
            if server_dir.is_dir() and (server_dir / "fixture.json").is_file():
                names.add(server_dir.name)
    return names


def _dataset_rel(submission_path: Path) -> str:
    for filename in _CONFIG_FILENAMES:
        cfg = _load_yaml_dict(submission_path / filename)
        dataset = cfg.get("dataset") or {}
        if isinstance(dataset, dict) and dataset.get("path"):
            return str(dataset["path"]).strip() or "cases"
    return "cases"


def _plugin_dirs_from_configs(submission_path: Path) -> list[str]:
    """Union of runner.plugin_dirs[0] across every config this submission
    commits, de-duplicated, order-preserving. A submission with only a
    single eval.yaml (AEH_MODE=single) contributes just that one entry --
    identical to the pre-extraction behavior."""
    seen: list[str] = []
    for filename in _CONFIG_FILENAMES:
        cfg = _load_yaml_dict(submission_path / filename)
        runner = cfg.get("runner") or {}
        if not isinstance(runner, dict):
            continue
        plugin_dirs = runner.get("plugin_dirs") or []
        if plugin_dirs:
            first = str(plugin_dirs[0])
            if first not in seen:
                seen.append(first)
    return seen


def _swap_entries(mcps_path: Path, mock_server_names: set[str], mock_binary: str) -> list[str]:
    """Rewrite matching server entries in mcps_path to the mocked stdio
    form. Backs up the pre-swap content to mcps.real.json alongside it
    (first call only -- never overwritten by a later idempotent re-run)."""
    data = json.loads(mcps_path.read_text())
    servers = data.get("mcpServers") or {}

    swapped: list[str] = []
    for name in sorted(mock_server_names):
        if name not in servers:
            continue
        servers[name] = {
            "type": "stdio",
            "command": "python3",
            "args": [
                mock_binary,
                "--schema",
                f"/workspace/mcp-servers/{name}/schema.json",
                "--fixtures",
                f"/workspace/mock-mcps/{name}/fixture.json",
                "--strategy",
                "fixtures",
                "--transport",
                "stdio",
            ],
        }
        swapped.append(name)

    if swapped:
        backup = mcps_path.parent / "mcps.real.json"
        if not backup.is_file():
            backup.write_text(mcps_path.read_text())
        mcps_path.write_text(json.dumps(data, indent=2))
    return swapped


def swap_mock_mcp_servers(submission_path: Path, mock_binary: str = DEFAULT_MOCK_BINARY) -> dict[str, list[str]]:
    """Swap mocked server entries in every real + MCP-only pack this
    submission's eval configs reference. Returns {pack_root: [swapped
    names]} for logging. Idempotent -- safe to call multiple times (e.g.
    once from the Tekton pipeline's swap-mock-mcp step and again, as a
    no-op, from a local run_aeh.py dev run that already ran it)."""
    submission_path = Path(submission_path)
    mock_server_names = _mock_server_names(submission_path / _dataset_rel(submission_path))
    if not mock_server_names:
        return {}

    plugin_dirs = _plugin_dirs_from_configs(submission_path)
    results: dict[str, list[str]] = {}
    real_pack_roots: dict[str, Path] = {}  # pack dir name -> root, for ones with a committed mcps.json

    # Pass 1: real packs (unchanged behavior from the original inline step).
    for rel in plugin_dirs:
        pack_root = (submission_path / rel).resolve()
        mcps_path = pack_root / "mcps.json"
        if not mcps_path.is_file():
            continue
        real_pack_roots[pack_root.name] = pack_root
        swapped = _swap_entries(mcps_path, mock_server_names, mock_binary)
        if swapped:
            results[str(pack_root)] = swapped

    # Pass 2: MCP-only siblings -- plugin_dirs entries with no mcps.json yet
    # whose name is "<real-pack-name>-mcp-only" for a real pack found above.
    # Generated fresh from that pack's pre-swap content every time this is
    # the first call to see it (mcps.real.json if pass 1 already swapped
    # the real pack this run, else the live mcps.json if pass 1 hasn't
    # touched it -- e.g. a submission whose real pack mocks no servers but
    # still wants an MCP-only control arm for an unmocked, real server).
    for rel in plugin_dirs:
        pack_root = (submission_path / rel).resolve()
        mcps_path = pack_root / "mcps.json"
        if mcps_path.is_file():
            continue  # not an MCP-only sibling needing generation
        if not pack_root.name.endswith(_MCP_ONLY_SUFFIX):
            continue
        real_name = pack_root.name[: -len(_MCP_ONLY_SUFFIX)]
        real_root = real_pack_roots.get(real_name)
        if real_root is None:
            logger.warning(
                "plugin_dirs '%s' looks like an MCP-only sibling of '%s', but no real "
                "pack named '%s' with a committed mcps.json was found among this "
                "submission's eval configs -- skipping (nothing generated)",
                rel,
                real_name,
                real_name,
            )
            continue
        source = real_root / "mcps.real.json"
        if not source.is_file():
            source = real_root / "mcps.json"
        pack_root.mkdir(parents=True, exist_ok=True)
        mcps_path.write_text(source.read_text())
        swapped = _swap_entries(mcps_path, mock_server_names, mock_binary)
        if swapped:
            results[str(pack_root)] = swapped

    return results


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission-path", required=True, type=Path)
    parser.add_argument("--mock-binary", default=DEFAULT_MOCK_BINARY)
    args = parser.parse_args(argv)

    results = swap_mock_mcp_servers(args.submission_path, args.mock_binary)
    if not results:
        print("No mcps.json server entries swapped")
        return 0
    for pack_root, names in results.items():
        print(f"Swapped {names} in {pack_root}/mcps.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
