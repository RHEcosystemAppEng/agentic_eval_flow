"""A release candidate cannot borrow another revision's or skill's evaluation."""

import json
import tarfile

import pytest

from abevalflow.results.konflux import component_evidence, package_batch
from abevalflow.results.run import write_json


@pytest.fixture
def component(tmp_path):
    revision = "a" * 40
    image = "quay.io/example/pack@sha256:" + "b" * 64
    directory = tmp_path / "batch"
    manifest = dict(
        schema_version="1.0",
        plugin_id="pack",
        source_revision=revision,
        payload_digest="sha256:" + "c" * 64,
        skills=["pack/demo"],
    )
    write_json(tmp_path / "manifest.json", manifest)
    write_json(
        directory / "batch.json",
        dict(
            schema_version="1.0",
            batch_id="batch",
            source_revision=revision,
            tiers=[1],
            persist=[],
            started_at="2026-11-04T10:00:00Z",
            completed_at="2026-11-04T10:01:00Z",
            items=[dict(skill_id="pack/demo", run_dir="runs/0000", run_id="run", status="completed", exit_code=0)],
        ),
    )
    write_json(
        directory / "runs/0000/run.json",
        dict(
            schema_version="1.1",
            run_id="run",
            skill="demo",
            source_revision=revision,
            evaluator_version="0.6.0",
            started_at="2026-11-04T10:00:00Z",
            completed_at="2026-11-04T10:01:00Z",
            config=dict(
                source="/source", skill="/source/pack/skills/demo", tiers=[1], persist=[], built_image_ref=image
            ),
            steps=[
                dict(name=name, status="completed", verdict="pass")
                for name in ["prepare", "structure", "security", "quality", "report"]
            ],
        ),
    )
    return directory, tmp_path / "manifest.json", image


def test_component_evidence_is_archived_before_oci_attachment(component):
    directory, manifest, image = component
    repository, digest = image.split("@")
    evidence = component_evidence(directory, manifest, repository, digest)
    package_batch(directory)
    with tarfile.open(directory / "reports.tar.gz") as archive:
        assert json.load(archive.extractfile("component-evaluation.json")) == evidence
    assert evidence["skills"] == ["pack/demo"]
    assert evidence["image_ref"] == image


@pytest.mark.parametrize("tamper", ["image", "coverage", "revision", "duplicate_steps"])
def test_component_evidence_rejects_mismatches(component, tamper):
    directory, manifest, image = component
    filename = directory / "runs/0000/run.json"
    run = json.loads(filename.read_text())
    if tamper == "image":
        run["config"]["built_image_ref"] = "quay.io/other/image@sha256:" + "d" * 64
    elif tamper == "revision":
        run["source_revision"] = "e" * 40
    elif tamper == "coverage":
        doc = json.loads(manifest.read_text())
        doc["skills"].append("pack/unevaluated")
        write_json(manifest, doc)
    else:
        run["steps"].append(run["steps"][0])
    write_json(filename, run)
    with pytest.raises(ValueError):
        component_evidence(directory, manifest, *image.split("@"))


def test_failed_nonblocking_checks_remain_failed_in_release_evidence(component):
    directory, manifest, image = component
    filename = directory / "runs/0000/run.json"
    run = json.loads(filename.read_text())
    run["steps"][2].update(verdict="fail", blocking=False)
    write_json(filename, run)
    evidence = component_evidence(directory, manifest, *image.split("@"))
    security = next(c for c in evidence["evaluations"][0]["checks"] if c["name"] == "security")
    assert security["verdict"] == "fail"


def test_component_identity_ignores_a_mutable_tag_but_retains_the_digest(component):
    directory, manifest, image = component
    tagged = image.replace("@", ":candidate@")
    filename = directory / "runs/0000/run.json"
    run = json.loads(filename.read_text())
    run["config"]["built_image_ref"] = tagged
    write_json(filename, run)
    evidence = component_evidence(directory, manifest, *tagged.split("@"))
    assert evidence["image_ref"] == image
