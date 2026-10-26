"""External runtime selection, process isolation, and installation failure contracts."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from abevalflow.execution.runtime import AEHRuntime, RuntimeUnavailable, install_runtime, resolve_runtime


def complete_runtime(root):
    runtime = AEHRuntime(root, Path(sys.executable))
    for path in [
        root / "agent_eval/harbor/run.py",
        *(
            root / "agent_eval/harbor/templates" / name
            for name in ("task.toml.tmpl", "instruction.md.tmpl", "test.sh.tmpl")
        ),
        runtime.task_generator,
        runtime.script("score.py"),
        runtime.script("report.py"),
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return runtime


def test_incomplete_wheel_installation_has_actionable_error(tmp_path):
    with pytest.raises(RuntimeUnavailable, match="abeval runtime install"):
        resolve_runtime(tmp_path, Path(sys.executable))


def test_external_process_uses_selected_interpreter_root_and_environment(tmp_path):
    runtime = complete_runtime(tmp_path / "upstream")
    output = tmp_path / "evidence"
    script = runtime.root / "probe.py"
    script.write_text("import os, pathlib; pathlib.Path(os.environ['EVIDENCE']).write_text(os.getcwd())")
    assert runtime.run([str(script)], environment={**os.environ, "EVIDENCE": str(output)}, timeout=10) == 0
    assert output.read_text() == str(runtime.root)
    project = tmp_path / "project"
    project.mkdir()
    assert runtime.run([str(script)], environment={**os.environ, "EVIDENCE": str(output)}, timeout=10, cwd=project) == 0
    assert output.read_text() == str(project)
    assert resolve_runtime(runtime.root, runtime.python) == runtime


def test_runtime_selection_preserves_virtualenv_python_symlink(tmp_path, monkeypatch):
    runtime = complete_runtime(tmp_path / "source")
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    monkeypatch.setenv("ABEVAL_AEH_ROOT", str(runtime.root))
    assert resolve_runtime().python == python


def test_failed_installation_is_retryable_and_existing_installation_is_preserved(tmp_path):
    destination = tmp_path / "external"
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "git")):
        with pytest.raises(subprocess.CalledProcessError):
            install_runtime(destination)
    assert not destination.exists()
    destination.mkdir()
    sentinel = destination / "keep"
    sentinel.write_text("existing installation")
    with pytest.raises(FileExistsError):
        install_runtime(destination)
    assert sentinel.read_text() == "existing installation"
