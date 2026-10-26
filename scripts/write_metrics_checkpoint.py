"""Compatibility launcher for the installed abevalflow package."""

from abevalflow.cli.compat.write_metrics_checkpoint import main

if __name__ == "__main__":
    raise SystemExit(main())
