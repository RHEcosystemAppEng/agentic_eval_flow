"""Artifact publication integrity and separation from evaluation verdicts."""

import json
import tarfile
from unittest.mock import MagicMock, patch

from abevalflow.config.runtime import resolve_config
from abevalflow.persistence.service import publish, publish_oci, publish_s3
from abevalflow.results.run import RunResult


def evidence(catalog, **options):
    config = resolve_config({**catalog, **options}, {})
    reports = config.output / "reports"
    reports.mkdir(parents=True)
    (reports / "scorecard.json").write_text("{}")
    (config.output / "kubeconfig").write_text("private-cluster-key")
    result = RunResult(run_id=config.run_id, skill=config.name, source_revision="rev", evaluator_version="0.1.0")
    return config, result


def test_s3_uploads_reports_and_manifest_but_not_source_or_kubeconfig(catalog):
    config, result = evidence(catalog, s3_endpoint="https://minio.example", s3_access_key="key", s3_secret_key="secret")
    client = MagicMock()
    with patch("minio.Minio", return_value=client):
        details = publish_s3(config, result)
    assert details["uri"] == "s3://ab-eval-reports/demo/test-run/"
    assert details["artifacts"] == 2
    uploaded = [str(c.args) for c in client.fput_object.call_args_list]
    assert not any("kubeconfig" in c or "source/" in c for c in uploaded)
    manifest = client.put_object.call_args.args[2]
    assert json.loads(manifest.getvalue())["run_id"] == config.run_id


def test_s3_creates_missing_bucket(catalog):
    config, result = evidence(catalog, s3_endpoint="http://minio.example", s3_access_key="key", s3_secret_key="secret")
    client = MagicMock()
    client.bucket_exists.return_value = False
    with patch("minio.Minio", return_value=client) as factory:
        publish_s3(config, result)
    client.make_bucket.assert_called_once_with("ab-eval-reports")
    assert factory.call_args.kwargs["secure"] is False


def test_oci_archive_contains_only_shareable_evidence(catalog):
    config, result = evidence(catalog, oci_subject="registry.example/image@sha256:" + "a" * 64)
    with patch("subprocess.run", return_value=MagicMock(stdout='{"digest": "sha256:artifact"}')) as command:
        details = publish_oci(config, result)
    with tarfile.open(config.output / "reports.tar.gz") as archive:
        assert "run.json" in archive.getnames()
        assert not any("kubeconfig" in name for name in archive.getnames())
    assert command.call_args.args[0][0] == "oras"
    assert details["artifact"]["digest"] == "sha256:artifact"


def test_publication_requires_explicit_destination(catalog):
    config, result = evidence(catalog)
    with patch("minio.Minio") as factory:
        assert publish(config, result) == []
    factory.assert_not_called()
