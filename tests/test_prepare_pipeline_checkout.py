"""Exercise the Prepare Task pipeline checkout with a local Git remote."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def test_pipeline_checkout_accepts_branch_tag_and_full_sha(tmp_path: Path):
    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    _git("init", "-q", "-b", "main", str(source))
    (source / "example.txt").write_text("main")
    _git("add", "example.txt", cwd=source)
    _git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "initial", cwd=source)
    main_sha = _git("rev-parse", "HEAD", cwd=source)
    _git("tag", "v1", cwd=source)
    _git("checkout", "-qb", "feature", cwd=source)
    (source / "example.txt").write_text("feature")
    _git("add", "example.txt", cwd=source)
    _git("-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-qm", "feature", cwd=source)
    feature_sha = _git("rev-parse", "HEAD", cwd=source)
    _git("init", "--bare", "-q", str(remote))
    _git("remote", "add", "origin", str(remote), cwd=source)
    _git("push", "-q", "origin", "main", "feature", "v1", cwd=source)

    repo = Path(__file__).resolve().parents[1]
    task = yaml.safe_load((repo / "pipeline/tasks/phases/prepare.yaml").read_text())
    script = next(step["script"] for step in task["spec"]["steps"] if step["name"] == "clone-pipeline-repo")

    def checkout(revision: str, workspace: Path) -> str:
        workspace.mkdir(exist_ok=True)
        rendered = (
            script.replace("$(workspaces.source.path)", str(workspace))
            .replace("$(params.pipeline-repo-revision)", revision)
            .replace("$(params.pipeline-repo-url)", str(remote))
        )
        subprocess.run(["bash", "-c", rendered], check=True, capture_output=True, text=True)
        return _git("rev-parse", "HEAD", cwd=workspace / "_pipeline")

    workspace = tmp_path / "workspace"
    assert checkout("main", workspace) == main_sha
    assert checkout("v1", workspace) == main_sha
    assert checkout("feature", workspace) == feature_sha
    assert checkout(main_sha, tmp_path / "fresh-workspace") == main_sha
