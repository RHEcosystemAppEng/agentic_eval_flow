"""Command interface for swap mock mcp."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from abevalflow.execution.mcp import DEFAULT_MOCK_BINARY, swap_mock_mcp_servers


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
