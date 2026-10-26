"""Launch the pinned upstream engine with packaged resources and MCP forwarding."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

RESOURCES = Path(__file__).resolve().parent.parent / "_vendor" / "aeh"


@lru_cache(maxsize=2)
def resource_module(name: str):
    """Load a packaged upstream resource under a stable module name."""
    path = RESOURCES / f"{name}.py"
    module_name = f"abevalflow_aeh_{name}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def configure_task_resources() -> None:
    """Provide task-generation templates omitted from the upstream wheel."""
    from agent_eval.harbor import tasks

    tasks._TEMPLATES = RESOURCES / "templates"


def configure_engine(engine) -> None:
    """Configure process-local upstream hooks without modifying installed dependency files."""
    from agent_eval.harbor import reward
    from agent_eval.harbor.run import resolve_plugin_dir

    reward._load_score_module = lambda: resource_module("score")
    engine._load_score_module = reward._load_score_module
    engine._load_report_module = lambda: resource_module("report")
    configure_task_resources()
    roots = engine._resolve_harbor_skill_roots
    mcp_configs: list[Path] = []

    def resolve_roots(config, agent_name):
        mcp_configs.clear()
        for configured in config.runner.plugin_dirs:
            path = resolve_plugin_dir(config, configured) / "mcps.json"
            if path.is_file():
                mcp_configs.append(path)
        return roots(config, agent_name)

    def launch(command, **kwargs):
        command = list(command)
        for path in mcp_configs:
            if str(path) not in command:
                command += ["--mcp-config", str(path)]
        return subprocess.Popen(command, **kwargs)

    engine._resolve_harbor_skill_roots = resolve_roots
    engine.subprocess = SimpleNamespace(**{**vars(subprocess), "Popen": launch})


def main() -> None:
    from agent_eval.harbor import run

    configure_engine(run)
    run.main()


if __name__ == "__main__":
    main()
