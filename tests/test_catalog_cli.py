"""Explicit catalog selection, tier planning, and independently executed phases."""

from unittest.mock import patch

import pytest

from abevalflow.cli.main import main
from abevalflow.config.runtime import ConfigurationError, resolve_config
from abevalflow.workflow.service import create_result, load_run, save_run


def options(catalog, **overrides):
    return {**catalog, "skill": None, "skill_id": "pack/demo", "tiers": "1,2", **overrides}


def initialize(options):
    from abevalflow.workflow.workspace import run_lock

    config = resolve_config(options, {})
    with run_lock(config.output, create=True):
        save_run(config, create_result(config))
    return config


@pytest.mark.parametrize(("tiers", "expected"), [("1", (1,)), ("2", (2,)), ("1,2", (1, 2))])
def test_catalog_runs_honor_requested_tiers(catalog, tiers, expected):
    config = initialize(options(catalog, tiers=tiers))
    assert config.tiers == expected
    assert ("evaluate" in load_run(config.output)[1].requested_steps) == (2 in expected)


@pytest.mark.parametrize("skill_id", ["../demo", "/pack/demo", "pack/demo/extra", "pack\\demo", "demo"])
def test_invalid_catalog_identifiers_are_rejected(catalog, skill_id):
    with pytest.raises(ConfigurationError):
        initialize(options(catalog, skill_id=skill_id))


def test_phase_if_selected_is_optional_but_does_not_silence_unknown_or_repeated_phases(catalog):
    config = initialize(options(catalog, tiers="1"))
    args = ["phase", "evaluate", "--run-dir", str(config.output)]
    assert main(args) == 2
    assert main([*args, "--if-selected"]) == 0
    assert load_run(config.output)[1].steps == []
    assert main(["phase", "prepare", "--run-dir", str(config.output)]) == 0
    assert main(["phase", "prepare", "--run-dir", str(config.output), "--if-selected"]) == 2


def test_cli_init_uses_catalog_identifier_and_explicit_tiers(catalog):
    assert (
        main(
            [
                "init",
                "--source",
                str(catalog["source"]),
                "--output",
                str(catalog["output"]),
                "--skill-id",
                "pack/demo",
                "--tiers",
                "2",
            ]
        )
        == 0
    )
    assert load_run(catalog["output"])[0].tiers == (2,)


def test_phased_static_run_uses_real_scanner_processes(catalog, tmp_path):
    scanner = tmp_path / "scanner"
    scanner.write_text(
        "#!/usr/bin/env python3\nimport json,pathlib,sys\n"
        "for i,arg in enumerate(sys.argv):\n"
        "    if arg in ('--output-json','--output-sarif','--output-security','--output-quality'):\n"
        "        pathlib.Path(sys.argv[i+1]).write_text(json.dumps({'findings':[]}))\n"
    )
    scanner.chmod(0o700)
    config = initialize(options(catalog, tiers="1", security_command=str(scanner), quality_command=str(scanner)))
    for name in (
        "prepare",
        "structure",
        "security",
        "quality",
        "submission",
        "submission-review",
        "evaluate",
        "report",
    ):
        assert main(["phase", name, "--if-selected", "--run-dir", str(config.output)]) == 0
    assert main(["publish", "--run-dir", str(config.output)]) == 0
    assert load_run(config.output)[1].exit_code == 0
    assert (config.output / "reports/scorecard.json").is_file()


def test_phase_commands_initialize_and_flush_optional_tracing(catalog):
    config = initialize(options(catalog, tiers="1"))
    with (
        patch("abevalflow.observability.otel.setup_tracer_provider") as setup,
        patch("abevalflow.observability.otel.shutdown_tracer_provider") as shutdown,
    ):
        assert main(["phase", "prepare", "--run-dir", str(config.output)]) == 0
    setup.assert_called_once_with()
    shutdown.assert_called_once_with()


def test_catalog_identifier_environment_is_supported(catalog):
    data = {k: v for k, v in catalog.items() if k != "skill"}
    config = resolve_config(data, {"ABEVAL_SKILL_ID": "pack/demo"})
    assert config.skill == catalog["skill"]


def test_catalog_identifier_and_path_are_mutually_exclusive(catalog):
    with pytest.raises(ConfigurationError, match="Choose a skill path"):
        resolve_config({**catalog, "skill_id": "pack/demo"}, {})
