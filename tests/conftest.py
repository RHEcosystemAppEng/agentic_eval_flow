"""Shared skill catalog fixtures for configuration and workflow contracts."""

import pytest


@pytest.fixture
def catalog(tmp_path):
    root = tmp_path / "catalog"
    skill = root / "pack/skills/demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Use when checking examples.\nlicense: Apache-2.0\n---\n# Demo\n"
    )
    submission = root / "submissions/pack/demo"
    submission.mkdir(parents=True)
    (submission / "metadata.yaml").write_text("name: demo\naeh_mode: pairwise\nskip_quality_review: false\n")
    return {"skill": skill, "source": root, "submission": submission, "output": tmp_path / "run", "run_id": "test-run"}
