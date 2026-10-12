"""Behavior tests for the OpenShell OIDC cache helper."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts import refresh_openshell_oidc as oidc


def test_initial_and_refresh_writes_are_atomic_and_private(tmp_path: Path):
    cache = tmp_path / "gateways" / "ci" / "oidc_token.json"
    issuer = "https://issuer.example"
    with patch.object(oidc.time, "time", return_value=1000):
        oidc.write_cache(cache, {"access_token": "first", "expires_in": 300}, issuer, "client")
    assert cache.stat().st_mode & 0o777 == 0o600
    assert json.loads(cache.read_text()) == {
        "access_token": "first",
        "expires_at": 1300,
        "issuer": issuer,
        "client_id": "client",
    }

    real_replace = os.replace

    def observe_replace(source: str, destination: Path) -> None:
        # A reader still sees the complete old cache until replacement.
        assert json.loads(cache.read_text())["access_token"] == "first"
        assert Path(source).stat().st_mode & 0o777 == 0o600
        real_replace(source, destination)

    with patch.object(oidc.os, "replace", side_effect=observe_replace):
        with patch.object(oidc.time, "time", return_value=1100):
            oidc.write_cache(cache, {"access_token": "second", "expires_in": 240}, issuer, "client")
    assert json.loads(cache.read_text())["access_token"] == "second"
    assert json.loads(cache.read_text())["expires_at"] == 1340
    assert cache.stat().st_mode & 0o777 == 0o600
    assert list(cache.parent.glob(".oidc-token-*")) == []


def test_failed_fetch_preserves_existing_cache(tmp_path: Path):
    cache = tmp_path / "oidc_token.json"
    oidc.write_cache(cache, {"access_token": "valid"}, "issuer", "client")
    original = cache.read_bytes()
    with patch.object(oidc, "fetch_token", side_effect=ValueError("bad response")):
        try:
            oidc.refresh(cache, "issuer", "client", "secret")
        except ValueError:
            pass
        else:
            raise AssertionError("refresh should fail")
    assert cache.read_bytes() == original


def test_evaluate_uses_one_helper_for_initial_and_periodic_refresh():
    task = yaml.safe_load((Path(__file__).parents[1] / "pipeline/tasks/phases/evaluate.yaml").read_text())
    step = next(item for item in task["spec"]["steps"] if item["name"] == "aeh-openshell-eval")
    script = step["script"]
    assert script.count('python3 "$OIDC_REFRESH_HELPER" --cache "$OIDC_TOKEN_CACHE"') == 2
    assert "--interval 120 &" in script
    assert script.index('python3 "$OIDC_REFRESH_HELPER" --cache "$OIDC_TOKEN_CACHE"') < script.index(
        'python "$PIPELINE_DIR/scripts/run_aeh.py"'
    )
