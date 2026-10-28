"""Artifact publication integrity and separation from evaluation verdicts."""

import json
from unittest.mock import MagicMock, patch

from abevalflow.config.runtime import resolve_config
from abevalflow.persistence.service import publish, publish_s3
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
    assert details["uri"] == "s3://ab-eval-reports/pack/demo/test-run/"
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


def test_publication_requires_explicit_destination(catalog):
    config, result = evidence(catalog)
    with patch("minio.Minio") as factory:
        assert publish(config, result) == []
    factory.assert_not_called()


def test_postgres_receives_uploaded_object_links_regardless_of_destination_order(pipeline_evidence):
    import hashlib

    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from abevalflow.db.models import EvidenceArtifact

    config, result, url = pipeline_evidence
    config = type(config).model_validate(
        config.model_dump()
        | {
            "persist": ("postgres", "s3"),
            "database_url": url,
            "s3_endpoint": "https://minio.example",
            "s3_access_key": "key",
            "s3_secret_key": "secret",
        }
    )
    with patch("minio.Minio", return_value=MagicMock()):
        outcomes = publish(config, result)
    assert all(s.status == "completed" for s in outcomes)
    manifest = json.loads((config.output / "run.json").read_text())
    assert {p["name"] for p in manifest["publication"]} == {"s3", "postgres"}
    with Session(create_engine(url)) as session:
        artifacts = session.scalars(select(EvidenceArtifact)).all()
        report = next(a for a in artifacts if a.name == "reports/report.json")
        assert report.uri.endswith("pack/demo/test-run/reports/report.json")
        assert report.sha256 == hashlib.sha256((config.output / "reports/report.json").read_bytes()).hexdigest()
        assert report.endpoint == "https://minio.example"
        assert len(artifacts) == sum(p.is_file() for p in (config.output / "reports").rglob("*")) + 1


def test_partial_s3_failure_still_persists_confirmed_uploads(pipeline_evidence):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from abevalflow.db.models import EvidenceArtifact, SkillEvaluation

    config, result, url = pipeline_evidence
    config = type(config).model_validate(
        config.model_dump()
        | {
            "persist": ("postgres", "s3"),
            "database_url": url,
            "s3_endpoint": "https://minio.example",
            "s3_access_key": "key",
            "s3_secret_key": "secret",
        }
    )
    client = MagicMock()
    client.fput_object.side_effect = [None, RuntimeError("secret-value")]
    with patch("minio.Minio", return_value=client):
        publish(config, result)
    assert "secret-value" not in result.model_dump_json()
    manifest = json.loads((config.output / "run.json").read_text())
    assert next(p for p in manifest["publication"] if p["name"] == "s3")["status"] == "error"
    with Session(create_engine(url)) as session:
        assert session.scalar(select(SkillEvaluation)) is not None
        assert len(session.scalars(select(EvidenceArtifact)).all()) == 1


def test_postgres_only_retry_preserves_existing_s3_links(pipeline_evidence):
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from abevalflow.db.models import EvidenceArtifact

    config, result, url = pipeline_evidence
    config = type(config).model_validate(
        config.model_dump()
        | {
            "persist": ("s3",),
            "database_url": url,
            "s3_endpoint": "https://minio.example",
            "s3_access_key": "key",
            "s3_secret_key": "secret",
        }
    )
    with patch("minio.Minio", return_value=MagicMock()):
        publish(config, result)
    config.persist = ("postgres",)
    with patch("minio.Minio") as client:
        outcomes = publish(config, result)
    client.assert_not_called()
    assert {s.name for s in outcomes} == {"s3", "postgres"}
    with Session(create_engine(url)) as session:
        assert session.scalars(select(EvidenceArtifact)).all()
