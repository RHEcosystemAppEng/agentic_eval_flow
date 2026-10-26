"""Tests for scripts/swap_mock_mcp.py.

Extracted from aeh-skill-eval.yaml's previously-untested inline swap-mock-mcp
heredoc -- see that module's docstring for the full mechanism this covers:
(1) real pack swap (original behavior) and (2) MCP-only sibling generation
(new, for pairwise control arms that need MCP access without SKILL.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from abevalflow.execution.mcp import swap_mock_mcp_servers


def _write_submission(
    tmp_path: Path,
    *,
    real_pack_mcps: dict,
    plugin_dirs_eval: list[str] | None = None,
    plugin_dirs_control: list[str] | None = None,
    plugin_dirs_treatment: list[str] | None = None,
    case_mock_servers: dict[str, list[str]] | None = None,
    real_pack_name: str = "rh-sre",
) -> Path:
    """Build submissions/<pack>/<skill>/ with cases/*/mock-mcps/<server>/
    fixture.json entries and a real pack root's mcps.json two levels up
    (mirroring plugin_dirs: ["../../../<pack>"] convention)."""
    submission = tmp_path / "submissions" / real_pack_name / "my-skill"
    submission.mkdir(parents=True)

    pack_root = tmp_path / real_pack_name
    pack_root.mkdir(parents=True)
    (pack_root / "mcps.json").write_text(json.dumps(real_pack_mcps, indent=2))

    case_mock_servers = case_mock_servers or {}
    for case_id, servers in case_mock_servers.items():
        for server in servers:
            fixture_dir = submission / "cases" / case_id / "mock-mcps" / server
            fixture_dir.mkdir(parents=True)
            (fixture_dir / "fixture.json").write_text(json.dumps({"sequence": []}))

    if plugin_dirs_eval is not None:
        (submission / "eval.yaml").write_text(
            yaml.safe_dump({"runner": {"plugin_dirs": plugin_dirs_eval}, "dataset": {"path": "cases"}})
        )
    if plugin_dirs_control is not None:
        (submission / "eval-control.yaml").write_text(
            yaml.safe_dump({"runner": {"plugin_dirs": plugin_dirs_control}, "dataset": {"path": "cases"}})
        )
    if plugin_dirs_treatment is not None:
        (submission / "eval-treatment.yaml").write_text(
            yaml.safe_dump({"runner": {"plugin_dirs": plugin_dirs_treatment}, "dataset": {"path": "cases"}})
        )
    return submission


def test_swaps_real_pack_entry_matching_mock_fixture(tmp_path: Path):
    """Original behavior: a plugin_dirs[0] pack root with a real mcps.json
    gets its matching server entry rewritten to the mocked stdio form."""
    submission = _write_submission(
        tmp_path,
        real_pack_mcps={"mcpServers": {"lightspeed-mcp": {"command": "podman", "args": ["run", "real-image"]}}},
        plugin_dirs_eval=["../../../rh-sre"],
        case_mock_servers={"case-001": ["lightspeed-mcp"]},
    )
    results = swap_mock_mcp_servers(submission, mock_binary="/opt/mock-mcp/src/server.py")

    pack_root = tmp_path / "rh-sre"
    assert str(pack_root) in results
    assert results[str(pack_root)] == ["lightspeed-mcp"]

    swapped = json.loads((pack_root / "mcps.json").read_text())
    entry = swapped["mcpServers"]["lightspeed-mcp"]
    assert entry["command"] == "python3"
    assert entry["args"][0] == "/opt/mock-mcp/src/server.py"
    assert "--schema" in entry["args"] and "/workspace/mcp-servers/lightspeed-mcp/schema.json" in entry["args"]
    assert "--fixtures" in entry["args"] and "/workspace/mock-mcps/lightspeed-mcp/fixture.json" in entry["args"]

    # Original content preserved for debugging, per module docstring.
    backup = json.loads((pack_root / "mcps.real.json").read_text())
    assert backup["mcpServers"]["lightspeed-mcp"]["command"] == "podman"


def test_unmocked_server_names_left_untouched(tmp_path: Path):
    submission = _write_submission(
        tmp_path,
        real_pack_mcps={
            "mcpServers": {
                "lightspeed-mcp": {"command": "podman", "args": ["run", "real-image"]},
                "aap-mcp-job-management": {"command": "python3", "args": ["aap-proxy.py"]},
            }
        },
        plugin_dirs_eval=["../../../rh-sre"],
        case_mock_servers={"case-001": ["lightspeed-mcp"]},
    )
    swap_mock_mcp_servers(submission, mock_binary="/opt/mock-mcp/src/server.py")

    swapped = json.loads((tmp_path / "rh-sre" / "mcps.json").read_text())
    # lightspeed-mcp mocked, aap-mcp-job-management (no fixture anywhere) untouched.
    assert swapped["mcpServers"]["lightspeed-mcp"]["command"] == "python3"
    assert swapped["mcpServers"]["aap-mcp-job-management"]["command"] == "python3"
    assert swapped["mcpServers"]["aap-mcp-job-management"]["args"] == ["aap-proxy.py"]


def test_generates_mcp_only_sibling_from_real_pack(tmp_path: Path):
    """A pairwise control arm's eval-control.yaml points plugin_dirs at
    '<real-pack>-mcp-only', which doesn't exist in git -- this function
    must create it from the real pack's own (pre-swap) mcps.json, then
    swap the copy the same way."""
    submission = _write_submission(
        tmp_path,
        real_pack_mcps={
            "mcpServers": {
                "lightspeed-mcp": {"command": "podman", "args": ["run", "real-image"]},
                "aap-mcp-job-management": {"command": "python3", "args": ["aap-proxy.py"]},
            }
        },
        plugin_dirs_eval=["../../../rh-sre"],
        plugin_dirs_control=["../../../rh-sre-mcp-only"],
        plugin_dirs_treatment=["../../../rh-sre"],
        case_mock_servers={"case-001": ["lightspeed-mcp"]},
    )
    results = swap_mock_mcp_servers(submission, mock_binary="/opt/mock-mcp/src/server.py")

    mcp_only_root = tmp_path / "rh-sre-mcp-only"
    assert mcp_only_root.is_dir()
    assert str(mcp_only_root) in results
    assert results[str(mcp_only_root)] == ["lightspeed-mcp"]

    generated = json.loads((mcp_only_root / "mcps.json").read_text())
    # Copied the full real pack (both server names), then mocked only the
    # one with a fixture -- same selection logic as the real pack.
    assert set(generated["mcpServers"]) == {"lightspeed-mcp", "aap-mcp-job-management"}
    assert generated["mcpServers"]["lightspeed-mcp"]["command"] == "python3"
    assert generated["mcpServers"]["aap-mcp-job-management"]["command"] == "python3"
    assert generated["mcpServers"]["aap-mcp-job-management"]["args"] == ["aap-proxy.py"]

    # No SKILL.md ever gets written here -- that's the entire point.
    assert not any(mcp_only_root.rglob("SKILL.md"))


def test_mcp_only_sibling_without_matching_real_pack_warns_and_skips(tmp_path: Path, caplog):
    """An MCP-only-looking plugin_dirs entry with no corresponding real
    pack anywhere in this submission's configs is skipped, not an error --
    mirrors the original step's tolerant 'nothing to swap' behavior."""
    submission = _write_submission(
        tmp_path,
        real_pack_mcps={"mcpServers": {"lightspeed-mcp": {"command": "podman", "args": []}}},
        plugin_dirs_control=["../../../totally-unrelated-mcp-only"],
        case_mock_servers={"case-001": ["lightspeed-mcp"]},
    )
    results = swap_mock_mcp_servers(submission, mock_binary="/opt/mock-mcp/src/server.py")
    assert results == {}
    assert not (tmp_path / "totally-unrelated-mcp-only").exists()


def test_noop_when_no_mock_server_names(tmp_path: Path):
    """No cases/*/mock-mcps/*/fixture.json anywhere -- nothing to swap,
    real pack's mcps.json left completely untouched."""
    submission = _write_submission(
        tmp_path,
        real_pack_mcps={"mcpServers": {"lightspeed-mcp": {"command": "podman", "args": ["run", "real-image"]}}},
        plugin_dirs_eval=["../../../rh-sre"],
        case_mock_servers={},
    )
    results = swap_mock_mcp_servers(submission, mock_binary="/opt/mock-mcp/src/server.py")
    assert results == {}
    original = json.loads((tmp_path / "rh-sre" / "mcps.json").read_text())
    assert original["mcpServers"]["lightspeed-mcp"]["command"] == "podman"


def test_idempotent_second_call(tmp_path: Path):
    """Calling twice (e.g. once from Tekton's swap-mock-mcp step, once from
    a local run_aeh.py dev run that already has a swapped checkout) is a
    safe no-op the second time -- doesn't double-wrap args or clobber the
    mcps.real.json backup."""
    submission = _write_submission(
        tmp_path,
        real_pack_mcps={"mcpServers": {"lightspeed-mcp": {"command": "podman", "args": ["run", "real-image"]}}},
        plugin_dirs_eval=["../../../rh-sre"],
        plugin_dirs_control=["../../../rh-sre-mcp-only"],
        case_mock_servers={"case-001": ["lightspeed-mcp"]},
    )
    first = swap_mock_mcp_servers(submission, mock_binary="/opt/mock-mcp/src/server.py")
    second = swap_mock_mcp_servers(submission, mock_binary="/opt/mock-mcp/src/server.py")
    assert first == second

    pack_root = tmp_path / "rh-sre"
    backup_before = (pack_root / "mcps.real.json").read_text()
    swap_mock_mcp_servers(submission, mock_binary="/opt/mock-mcp/src/server.py")
    assert (pack_root / "mcps.real.json").read_text() == backup_before
