"""Application boundaries and pinned upstream resource consistency."""

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_compatibility_scripts_only_launch_packaged_commands():
    for path in (ROOT / "scripts").glob("*.py"):
        tree = ast.parse(path.read_text())
        assert not any(isinstance(node, (ast.FunctionDef, ast.ClassDef)) for node in tree.body), path
        assert len(tree.body) <= 3, path


def test_application_services_do_not_import_scripts_or_cli():
    for path in (ROOT / "abevalflow").rglob("*.py"):
        if any(part in path.parts for part in ("cli", "_vendor")) or path.name == "__main__.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("scripts", "abevalflow.cli")), path


def test_external_aeh_installations_use_the_same_pin_without_bundled_code():
    from abevalflow.execution.runtime import AEH_REVISION

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert not any("agent-eval-harness" in d for d in project["project"]["optional-dependencies"]["runner"])
    for image in ("aeh-trial", "agent-eval-harness"):
        assert AEH_REVISION in (ROOT / "containers" / image / "Containerfile").read_text()
    assert not (ROOT / "abevalflow/_vendor").exists()
    assert not (ROOT / "abevalflow/execution/upstream.py").exists()
