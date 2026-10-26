"""Compatibility launcher for the installed abevalflow package."""

from abevalflow.cli.compat.swap_mock_mcp import main

if __name__ == "__main__":
    raise SystemExit(main())
