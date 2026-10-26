"""Compatibility launcher for the installed abevalflow package."""

from abevalflow.cli.compat.analyze import main

if __name__ == "__main__":
    raise SystemExit(main())
