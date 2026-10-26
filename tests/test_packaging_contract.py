"""Application boundaries and pinned upstream resource consistency."""

import ast
import hashlib
import json
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


def test_aehruntime_pins_and_resource_checksums_agree():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    manifest = json.loads((ROOT / "abevalflow/_vendor/aeh/manifest.json").read_text())
    runner = project["project"]["optional-dependencies"]["runner"]
    assert any(manifest["commit"] in dependency for dependency in runner)
    assert manifest["commit"] in (ROOT / "containers/aeh-trial/Containerfile").read_text()
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((ROOT / "abevalflow/_vendor/aeh" / name).read_bytes()).hexdigest() == digest
