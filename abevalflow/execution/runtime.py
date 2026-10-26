"""Install and invoke a complete, unchanged upstream AEH distribution."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

AEH_REPOSITORY = "https://github.com/fullsend-ai/agent-eval-harness.git"
AEH_VERSION = "1.57.1"
AEH_REVISION = "8a15a8b687b5561903827805eaae8b438bde9954"
HARBOR_VERSION = "0.24.0"


class RuntimeUnavailable(ValueError):
    """The configured external evaluation runtime is incomplete."""


def default_installation() -> Path:
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache / "abeval" / "aeh" / AEH_REVISION


@dataclass(frozen=True)
class AEHRuntime:
    """A complete upstream source distribution and its Python interpreter."""

    root: Path
    python: Path

    def script(self, name: str) -> Path:
        return self.root / "skills/eval-run/scripts" / name

    @property
    def task_generator(self) -> Path:
        return self.root / "skills/eval-dataset/scripts/harbor.py"

    def validate(self) -> None:
        required = [
            self.python,
            self.root / "agent_eval/harbor/run.py",
            self.root / "agent_eval/harbor/templates/task.toml.tmpl",
            self.root / "agent_eval/harbor/templates/instruction.md.tmpl",
            self.root / "agent_eval/harbor/templates/test.sh.tmpl",
            self.task_generator,
            self.script("score.py"),
            self.script("report.py"),
        ]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise RuntimeUnavailable(
                "Complete AEH runtime required. Run 'abeval runtime install', or set "
                "ABEVAL_AEH_ROOT and ABEVAL_AEH_PYTHON. Missing: " + ", ".join(missing)
            )

    def run(
        self, arguments: list[str], *, environment: dict[str, str] | None, timeout: int, cwd: Path | None = None
    ) -> int:
        """Execute an upstream command with an explicit evaluation project directory."""
        from abevalflow.execution.process import run_process

        self.validate()
        return run_process(
            [str(self.python), *arguments],
            cwd=cwd or self.root,
            env=environment,
            timeout=timeout,
            stdout=sys.stderr,
        )


def resolve_runtime(root: Path | None = None, python: Path | None = None) -> AEHRuntime:
    """Resolve explicit settings, a source installation, or the managed runtime."""
    configured = root or os.environ.get("ABEVAL_AEH_ROOT")
    if configured:
        source = Path(configured).expanduser().resolve()
    else:
        source = default_installation() / "source"
        try:
            spec = importlib.util.find_spec("agent_eval")
        except (ImportError, ValueError):
            spec = None
        if spec and spec.origin:
            installed = Path(spec.origin).resolve().parent.parent
            if (installed / "skills/eval-run/scripts/score.py").is_file():
                source = installed
    configured_python = python or os.environ.get("ABEVAL_AEH_PYTHON")
    sibling = source.parent / ".venv/bin/python"
    interpreter = (
        Path(configured_python).expanduser().absolute()
        if configured_python
        else (sibling if sibling.is_file() else Path(sys.executable))
    )
    runtime = AEHRuntime(source, interpreter)
    runtime.validate()
    return runtime


def install_runtime(directory: Path | None = None, python: Path | None = None) -> AEHRuntime:
    """Install the pinned upstream checkout and its dependencies in a private venv."""
    destination = (directory or default_installation()).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=False)
    source = destination / "source"
    interpreter = python or Path(sys.executable)
    commands = [
        ["git", "init", str(source)],
        ["git", "-C", str(source), "remote", "add", "origin", AEH_REPOSITORY],
        ["git", "-C", str(source), "fetch", "--depth", "1", "origin", AEH_REVISION],
        ["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"],
        [str(interpreter), "-m", "venv", str(destination / ".venv")],
        [
            str(destination / ".venv/bin/python"),
            "-m",
            "pip",
            "install",
            "--editable",
            f"{source}[harbor,anthropic,openai]",
            f"harbor=={HARBOR_VERSION}",
        ],
    ]
    try:
        for command in commands:
            subprocess.run(command, check=True, timeout=1800, stdout=sys.stderr)
        runtime = AEHRuntime(source, destination / ".venv/bin/python")
        runtime.validate()
        return runtime
    except BaseException:
        shutil.rmtree(destination)
        raise
