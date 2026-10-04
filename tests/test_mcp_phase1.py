"""Tests for MCP Phase 1 static / build-time checks.

Two layers:

- Unit tests exercise the normalization functions and the gate pass/fail logic
  with synthetic scan JSON. No external tools required - safe in any CI.
- Integration tests build a fixture repo in a temp dir and run the real scanner
  (gitleaks / semgrep / licensee) end-to-end. Skipped when the tool is absent.
"""

from __future__ import annotations

import json
import secrets
import shutil
import string
from pathlib import Path

import pytest

from abevalflow.mcp.phase1 import (
    DEFAULT_ALLOWED_LICENSES,
    LicenseGate,
    NoUserCodeGate,
    SecretsGate,
    run_phase1,
)
from abevalflow.schemas import GateMode, GatePolicy
from scripts.mcp import license_scan, no_user_code_scan, secrets_scan

BLOCK = GatePolicy(default_mode=GateMode.BLOCK)


def _write(reports_dir: Path, filename: str, findings: list[dict]) -> None:
    (reports_dir / filename).write_text(json.dumps({"findings": findings}))


# ---------------------------------------------------------------------------
# Normalization unit tests (no external tools)
# ---------------------------------------------------------------------------


def test_secrets_normalize_maps_fields_and_severity():
    raw = [
        {"RuleID": "aws-access-token", "Description": "AWS key", "File": "a.py", "StartLine": 3},
        {"RuleID": "generic-api-key", "Description": "generic", "File": "b.py", "StartLine": 7},
    ]
    findings = secrets_scan.normalize(raw)
    assert [f["rule_id"] for f in findings] == ["aws-access-token", "generic-api-key"]
    assert findings[0]["severity"] == "high"
    assert findings[1]["severity"] == "high"  # every gitleaks hit is HIGH
    assert findings[0]["file_path"] == "a.py"


def test_no_user_code_normalize_maps_semgrep_severity():
    raw = [
        {
            "check_id": "python-dynamic-exec",
            "path": "bad.py",
            "start": {"line": 2},
            "extra": {"severity": "ERROR", "message": "eval used"},
        }
    ]
    findings = no_user_code_scan.normalize(raw)
    assert findings[0]["severity"] == "high"
    assert findings[0]["rule_id"] == "python-dynamic-exec"
    assert findings[0]["file_path"] == "bad.py"


def test_license_allowed_produces_no_findings():
    detected = [{"spdx_id": "MIT"}]
    findings = license_scan.evaluate_licenses(detected, {"mit", "apache-2.0"}, [])
    assert findings == []


def test_license_disallowed_flags_high():
    detected = [{"spdx_id": "GPL-3.0"}]
    matched = [{"filename": "LICENSE"}]
    findings = license_scan.evaluate_licenses(detected, {"mit"}, matched)
    assert len(findings) == 1
    assert findings[0]["severity"] == "high"
    assert findings[0]["rule_id"] == "license-not-allowed"
    assert findings[0]["file_path"] == "LICENSE"


def test_license_missing_flags_high():
    findings = license_scan.evaluate_licenses([], {"mit"}, [])
    assert findings[0]["rule_id"] == "license-missing"


# ---------------------------------------------------------------------------
# Gate logic tests (synthetic scan JSON)
# ---------------------------------------------------------------------------


def test_secrets_gate_fails_on_high_in_block_mode(tmp_path):
    _write(tmp_path, "secrets-scan.json", [{"severity": "high", "message": "leak", "rule_id": "r"}])
    result = SecretsGate().evaluate(tmp_path, BLOCK)
    assert result.passed is False


def test_secrets_gate_passes_when_clean(tmp_path):
    _write(tmp_path, "secrets-scan.json", [])
    result = SecretsGate().evaluate(tmp_path, BLOCK)
    assert result.passed is True
    assert result.score == 1.0


def test_run_phase1_all_clean_passes(tmp_path):
    _write(tmp_path, "secrets-scan.json", [])
    _write(tmp_path, "no-user-code-scan.json", [])
    _write(tmp_path, "license-scan.json", [])
    results = run_phase1(tmp_path, BLOCK)
    assert len(results) == 3
    assert all(r.passed for r in results)
    assert {r.policy_key for r in results} == {"mcp-secrets", "mcp-no-user-code", "mcp-license"}


def test_run_phase1_one_dirty_fails_that_gate(tmp_path):
    _write(tmp_path, "secrets-scan.json", [])
    _write(tmp_path, "no-user-code-scan.json", [{"severity": "high", "message": "eval", "rule_id": "x"}])
    _write(tmp_path, "license-scan.json", [])
    results = {r.policy_key: r for r in run_phase1(tmp_path, BLOCK)}
    assert results["mcp-secrets"].passed is True
    assert results["mcp-no-user-code"].passed is False
    assert results["mcp-license"].passed is True


# ---------------------------------------------------------------------------
# Integration tests (real tools, skipped when absent)
# ---------------------------------------------------------------------------

_MIT = """MIT License

Copyright (c) 2026 Example

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


@pytest.mark.skipif(not shutil.which("gitleaks"), reason="gitleaks not installed")
def test_integration_secrets_detects_planted_key(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    # Synthetic key generated at runtime - no hardcoded credential in this source
    # (only the public "AKIA" prefix). gitleaks matches by pattern, so a generated
    # AKIA+16 key still trips its AWS rule; the full value lives only in the temp file.
    access_key = "AKIA" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(16))
    secret_key = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(40))
    (repo / "config.py").write_text(f'AWS_ACCESS_KEY_ID = "{access_key}"\naws_secret = "{secret_key}"\n')
    reports = tmp_path / "reports"
    reports.mkdir()
    rc = _run_scanner(secrets_scan, [str(repo), "--reports-dir", str(reports)])
    assert rc == 0
    result = SecretsGate().evaluate(reports, BLOCK)
    assert result.passed is False
    assert result.findings


@pytest.mark.skipif(not shutil.which("semgrep"), reason="semgrep not installed")
def test_integration_no_user_code_detects_eval(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bad.py").write_text("def handler(x):\n    return eval(x)\n")
    reports = tmp_path / "reports"
    reports.mkdir()
    rc = _run_scanner(no_user_code_scan, [str(repo), "--reports-dir", str(reports)])
    assert rc == 0
    result = NoUserCodeGate().evaluate(reports, BLOCK)
    assert result.passed is False


@pytest.mark.skipif(not shutil.which("semgrep"), reason="semgrep not installed")
def test_integration_no_user_code_clean_passes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "good.py").write_text("def handler(x):\n    return x.upper()\n")
    reports = tmp_path / "reports"
    reports.mkdir()
    rc = _run_scanner(no_user_code_scan, [str(repo), "--reports-dir", str(reports)])
    assert rc == 0
    result = NoUserCodeGate().evaluate(reports, BLOCK)
    assert result.passed is True


@pytest.mark.skipif(not shutil.which("licensee"), reason="licensee not installed")
def test_integration_license_mit_passes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "LICENSE").write_text(_MIT)
    reports = tmp_path / "reports"
    reports.mkdir()
    rc = _run_scanner(
        license_scan,
        [str(repo), "--reports-dir", str(reports), *_allow_args()],
    )
    assert rc == 0
    result = LicenseGate().evaluate(reports, BLOCK)
    assert result.passed is True


def _allow_args() -> list[str]:
    args: list[str] = []
    for spdx in DEFAULT_ALLOWED_LICENSES:
        args += ["--allow", spdx]
    return args


def _run_scanner(module, argv: list[str]) -> int:
    """Invoke a scanner module's main() with a patched argv."""
    import sys

    old = sys.argv
    sys.argv = [module.__name__, *argv]
    try:
        return module.main()
    finally:
        sys.argv = old
