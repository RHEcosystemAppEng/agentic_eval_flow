import json
import re
from pathlib import Path

from scripts.build_forge_draft_cases import build


def test_fixture_generator_rejects_image_schema_errors(tmp_path):
    import pytest

    from scripts.build_forge_fixtures import validate_evidence

    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/publish-brief.mjs").write_text(
        'export const validateBriefEvidence = () => ["invalid unavailable reason"];'
    )
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}")
    with pytest.raises(ValueError, match="invalid unavailable reason"):
        validate_evidence(tmp_path, evidence)


def test_committed_draft_cases_match_generator_and_allow_date_abbreviations(tmp_path):
    assert build(tmp_path) == 11
    committed = Path(__file__).parents[1] / "submissions/openclaw-forge-drafts/cases"
    for generated in (tmp_path / "cases").rglob("*"):
        if generated.is_file():
            assert generated.read_bytes() == (committed / generated.relative_to(tmp_path / "cases")).read_bytes()
    expected = json.loads((tmp_path / "cases/tone-revision/draft-expectations.json").read_text())
    pattern = expected["content"][0]["body_patterns"][-1]
    for deadline in ("September 28", "Sep 28", "Sept. 28", "28 Sep"):
        assert re.search(pattern, deadline)
