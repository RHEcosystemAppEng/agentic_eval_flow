#!/usr/bin/env python3
"""Build-time patch: forward MCP server config to Harbor's ``--mcp-config``.

WHY: agent_eval.harbor.run (vendored from AEH_REPO at the pinned AEH_SHA,
see ../Containerfile) resolves each ``runner.plugin_dirs`` entry and
forwards Harbor's ``--skill`` option (_resolve_harbor_skill_roots), but
never forwards any MCP server configuration -- even though Harbor's own
``harbor run --help`` documents ``--mcp-config <path>``: "Path to a
Claude-style .mcp.json or Harbor MCP config file. Can be used multiple
times." Without this, every Claude Code eval session's stream-json init
event reports `mcp_servers: []` regardless of mock-vs-real MCP wiring
downstream, since the agent is never told about any MCP server at all.

Every pack's ``mcps.json`` (e.g. rh-sre/mcps.json) is already in exactly
the "Claude-style .mcp.json" shape ``--mcp-config`` expects
(``{"mcpServers": {...}}``) -- no new file format needed, just forwarding.

This script patches agent_eval/harbor/run.py in-place at image build time
(applied by ../Containerfile right after `git checkout ${AEH_SHA}`, before
`rm -rf .git`): adds `_resolve_harbor_mcp_configs()` mirroring the existing
`_resolve_harbor_skill_roots()`, and forwards `--mcp-config <path>` for
each pack that has one, alongside the existing `--skill` forwarding.

Anchored on exact source strings (not line numbers) so a future AEH_SHA
bump either patches cleanly or fails the build loudly -- never silently
no-ops.
"""
import sys
from pathlib import Path

TARGET = Path(sys.argv[1] if len(sys.argv) > 1 else
              "/opt/agent-eval-harness/agent_eval/harbor/run.py")

FUNC_ANCHOR = (
    "            print(f\"WARNING: plugin exports no skills; not forwarding {path} \"\n"
    "                  f\"to the Harbor {agent_name} agent\", file=sys.stderr)\n"
    "    return roots\n"
)

NEW_FUNC = (
    "\n\n"
    "def _resolve_harbor_mcp_configs(config: EvalConfig) -> list[Path]:\n"
    "    \"\"\"Resolve per-pack mcps.json files for Harbor's ``--mcp-config`` option.\n"
    "\n"
    "    Mirrors _resolve_harbor_skill_roots above: each runner.plugin_dirs entry\n"
    "    is a pack root (e.g. rh-sre/) that may commit its own mcps.json --\n"
    "    already in the \"Claude-style .mcp.json\" shape ({\"mcpServers\": {...}})\n"
    "    harbor run --help documents for --mcp-config. Packs with no mcps.json\n"
    "    (no MCP servers declared) are silently skipped, same tolerance as the\n"
    "    skill-roots resolver for plugins that export no skills.\n"
    "    \"\"\"\n"
    "    configs: list[Path] = []\n"
    "    for configured in config.runner.plugin_dirs:\n"
    "        path = resolve_plugin_dir(config, configured)\n"
    "        mcps_path = path / \"mcps.json\"\n"
    "        if mcps_path.is_file():\n"
    "            configs.append(mcps_path)\n"
    "    return configs\n"
)

CMD_ANCHOR = (
    "    for root in _resolve_harbor_skill_roots(config, agent_name):\n"
    "        cmd += [\"--skill\", str(root)]\n"
)

CMD_INSERT = (
    "    for root in _resolve_harbor_skill_roots(config, agent_name):\n"
    "        cmd += [\"--skill\", str(root)]\n"
    "    for mcp_config in _resolve_harbor_mcp_configs(config):\n"
    "        cmd += [\"--mcp-config\", str(mcp_config)]\n"
)

MARKER = "_resolve_harbor_mcp_configs"


def main() -> int:
    if not TARGET.is_file():
        print(f"ERROR: patch target not found: {TARGET}", file=sys.stderr)
        return 1

    text = TARGET.read_text()

    if MARKER in text:
        print(f"OK: {TARGET} already patched (found {MARKER!r}) -- no-op")
        return 0

    if text.count(FUNC_ANCHOR) != 1:
        print(
            f"ERROR: expected exactly 1 occurrence of the function-insertion "
            f"anchor in {TARGET}, found {text.count(FUNC_ANCHOR)}. "
            "agent_eval/harbor/run.py source has drifted from what this patch "
            "was written against -- update patch_mcp_config.py's anchors "
            "before bumping AEH_SHA further.",
            file=sys.stderr,
        )
        return 1

    if text.count(CMD_ANCHOR) != 1:
        print(
            f"ERROR: expected exactly 1 occurrence of the cmd-insertion anchor "
            f"in {TARGET}, found {text.count(CMD_ANCHOR)}. "
            "agent_eval/harbor/run.py source has drifted from what this patch "
            "was written against -- update patch_mcp_config.py's anchors "
            "before bumping AEH_SHA further.",
            file=sys.stderr,
        )
        return 1

    text = text.replace(FUNC_ANCHOR, FUNC_ANCHOR + NEW_FUNC, 1)
    text = text.replace(CMD_ANCHOR, CMD_INSERT, 1)
    TARGET.write_text(text)
    print(f"OK: patched {TARGET} -- added _resolve_harbor_mcp_configs() + --mcp-config forwarding")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
