"""Compatibility launcher for the installed abevalflow package."""

from abevalflow.cli.compat.run_aeh import main

if __name__ == "__main__":
    raise SystemExit(main())
