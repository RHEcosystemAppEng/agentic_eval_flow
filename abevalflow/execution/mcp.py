"""Execution mcp services."""

from __future__ import annotations

import json
import logging
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
    real_pack_roots: dict[str, Path] = {}

    for rel in plugin_dirs:
        pack_root = (submission_path / rel).resolve()
        mcps_path = pack_root / "mcps.json"
        if not mcps_path.is_file():
            continue
        real_pack_roots[pack_root.name] = pack_root
        swapped = _swap_entries(mcps_path, mock_server_names, mock_binary)
        if swapped:
            results[str(pack_root)] = swapped

    for rel in plugin_dirs:
        pack_root = (submission_path / rel).resolve()
        mcps_path = pack_root / "mcps.json"
        if mcps_path.is_file():
            continue
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
