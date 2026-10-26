"""Compatibility launcher for the installed abevalflow package."""

from abevalflow.cli.compat.aggregate_scorecard import main

if __name__ == "__main__":
    raise SystemExit(main())
