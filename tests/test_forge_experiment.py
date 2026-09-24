import json

import pytest

from abevalflow.forge_compare import build_pair
from scripts.compare_forge_experiment import verify_infrastructure


def test_pair_rejects_changed_loaded_identity(tmp_path):
    for arm in ("baseline", "candidate"):
        root = tmp_path / arm
        root.mkdir()
        (root / "run_result.json").write_text(
            json.dumps({"evaluation_status": "passed", "usage": {"complete": True, "output": 10}})
        )
        (root / "provenance.json").write_text(
            json.dumps(
                {
                    "image": "digest",
                    "model": "model",
                    "effort": "high",
                    "prompt_sha256": "p",
                    "harness_files": {"runtime": "hash"},
                    "model_config_sha256": "m",
                    "initial_files": {},
                    "loaded_files": {"USER.md": arm, "skills/forge-drafts/SKILL.md": arm},
                }
            )
        )
    with pytest.raises(ValueError, match="more than the intended skill"):
        build_pair(
            tmp_path / "baseline",
            tmp_path / "candidate",
            case="test",
            pair=0,
            skill_path="skills/forge-drafts/SKILL.md",
        )


def test_quality_failure_allowed_but_storage_failure_is_not(tmp_path):
    record = {
        "run": "run",
        "directory": str(tmp_path),
        "status": "Failed",
        "mlflow_verified": True,
        "stages": {k: "True" for k in ("prepare", "evaluate", "analyze", "store")},
    }
    with pytest.raises(OSError):
        verify_infrastructure(record)
    (tmp_path / "storage-attestation.json").write_text(
        json.dumps({"run": "run", "status": "verified", "objects": [{"key": "artifact"}]})
    )
    verify_infrastructure(record)
    record["stages"]["store"] = "False"
    with pytest.raises(ValueError, match="infrastructure"):
        verify_infrastructure(record)
