"""Execution backend selection and public runner interfaces."""

from __future__ import annotations

from typing import Any

from abevalflow.execution.artifacts import (
    eval_dir_name as eval_dir_name,
)
from abevalflow.execution.artifacts import (
    materialize_aeh_case_outputs as materialize_aeh_case_outputs,
)
from abevalflow.execution.artifacts import (
    resolve_aeh_run_dir as resolve_aeh_run_dir,
)
from abevalflow.execution.base import BaseRunner as BaseRunner
from abevalflow.execution.base import RunnerError as RunnerError
from abevalflow.execution.harbor import HarborRunner
from abevalflow.execution.resources import find_aeh_script as find_aeh_script

RUNNERS: dict[str, type[BaseRunner]] = {
    "harbor": HarborRunner,
}


def get_runner(name: str, **kwargs: Any) -> BaseRunner:
    """Get a runner instance by name.

    Args:
        name: Runner name (harbor)
        **kwargs: Arguments passed to runner constructor

    Returns:
        Runner instance

    Raises:
        ValueError: If runner name is not recognized
    """
    if name not in RUNNERS:
        available = ", ".join(RUNNERS.keys())
        raise ValueError(f"Unknown runner '{name}'. Available: {available}")
    return RUNNERS[name](**kwargs)
