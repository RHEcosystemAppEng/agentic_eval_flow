"""Publish evidence independently, then project uploaded artifact references into PostgreSQL."""

from __future__ import annotations

import hashlib
import io
import mimetypes
from urllib.parse import urlparse

from abevalflow.config.runtime import RuntimeConfig
from abevalflow.results.run import RunResult, StepResult, write_json


class S3UploadError(RuntimeError):
    """Retain references to objects already uploaded when a later upload fails."""

    def __init__(self, details: dict):
        super().__init__("S3 publication did not complete")
        self.details = details


def publish(config: RuntimeConfig, result: RunResult) -> list[StepResult]:
    """Attempt all selected destinations. Publish modified reports and S3 objects before the DB snapshot."""
    order = {"compass": 0, "s3": 1, "postgres": 2}
    outcomes = {step.name: step for step in result.publication}
    # Preserve previous, unselected destinations during a targeted publication retry.
    names = list(dict.fromkeys([*config.persist, *outcomes]))

    def record(step: StepResult) -> None:
        outcomes[step.name] = step
        result.publication = [outcomes[name] for name in names if name in outcomes]
        write_json(config.output / "run.json", result.model_dump(mode="json"))

    for destination in sorted(config.persist, key=lambda d: order[d]):
        try:
            if destination == "postgres":
                from abevalflow.persistence.database import database_connection
                from abevalflow.persistence.postgres import store_snapshot

                if not config.database_url:
                    raise ValueError("Postgres requires ABEVAL_DATABASE_URL or DATABASE_URL")
                with database_connection(
                    config.database_url.get_secret_value(),
                    config.database_tls_route_host,
                    verify_tls=not config.insecure_ssl,
                ) as url:
                    store_snapshot(config, result, url)
                details = {}
            elif destination == "s3":
                details = publish_s3(config, result)
            elif destination == "compass":
                from abevalflow.persistence.compass import publish_scorecard
                from abevalflow.report import AnalysisResult
                from abevalflow.scorecard import Scorecard

                path = config.output / "reports/scorecard.json"
                scorecard = Scorecard.model_validate_json(path.read_text())
                if not scorecard.policy.push_facts:
                    raise ValueError("Compass requires gate_policy.push_facts in submission metadata")
                if config.metadata and config.metadata.gate_policy and config.metadata.gate_policy.push_facts:
                    scorecard.policy.push_facts = config.metadata.gate_policy.push_facts.model_copy(deep=True)
                if config.compass_token:
                    scorecard.policy.push_facts.bearer_token = config.compass_token.get_secret_value()
                report_path = config.output / "reports/report.json"
                report = AnalysisResult.model_validate_json(report_path.read_text()) if report_path.is_file() else None
                success = publish_scorecard(scorecard, verify_tls=not config.insecure_ssl, result=result, report=report)
                scorecard.policy.push_facts.bearer_token = "${COMPASS_API_TOKEN}"
                write_json(path, scorecard.model_dump(mode="json"))
                if not success:
                    raise RuntimeError("One or more Compass facts could not be published")
                details = {}
            else:
                raise ValueError(f"Unsupported persistence destination: {destination}")
            record(StepResult(name=destination, blocking=config.persistence_required, details=details))
        except Exception as exc:
            # SDK exception text can contain credentials or request headers.
            record(
                StepResult(
                    name=destination,
                    blocking=config.persistence_required,
                    status="error",
                    verdict="unavailable",
                    reason=f"{destination} publication failed ({type(exc).__name__})",
                    details=exc.details if isinstance(exc, S3UploadError) else {},
                )
            )
    return result.publication


def publish_s3(config: RuntimeConfig, result: RunResult) -> dict:
    """Upload reports with durable object references, hashes, and stable retry-safe keys."""
    from minio import Minio

    if not (config.s3_endpoint and config.s3_access_key and config.s3_secret_key):
        raise ValueError("S3 requires endpoint, access key, and secret key")
    parsed = urlparse(config.s3_endpoint if "://" in config.s3_endpoint else f"https://{config.s3_endpoint}")
    if parsed.username or parsed.password:
        raise ValueError("Supply S3 credentials separately from the endpoint")
    client = Minio(
        parsed.netloc,
        access_key=config.s3_access_key.get_secret_value(),
        secret_key=config.s3_secret_key.get_secret_value(),
        secure=parsed.scheme == "https",
        cert_check=config.s3_verify_tls,
    )
    prefix = (
        f"{config.plugin}/{config.skill.name}/{config.run_id}" if config.plugin else f"{config.name}/{config.run_id}"
    )
    uri = f"s3://{config.s3_bucket}/{prefix}/"
    # Retain previously uploaded objects when a targeted retry encounters a failure.
    objects = {
        obj["uri"]: obj
        for step in result.publication
        if step.name == "s3"
        for obj in step.details.get("objects", [])
        if obj["uri"].startswith(uri)
    }

    def details():
        return {"uri": uri, "artifacts": len(objects), "objects": list(objects.values())}

    def artifact(name, digest, size):
        return {
            "name": name,
            "uri": f"{uri}{name}",
            "endpoint": f"{parsed.scheme}://{parsed.netloc}",
            "sha256": digest,
            "size_bytes": size,
            "media_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
        }

    try:
        if not client.bucket_exists(config.s3_bucket):
            client.make_bucket(config.s3_bucket)
        reports = config.output / "reports"
        for path in sorted(reports.rglob("*")):
            if path.is_file() and not path.is_symlink():
                name = f"reports/{path.relative_to(reports).as_posix()}"
                with path.open("rb") as data:
                    digest = hashlib.file_digest(data, "sha256").hexdigest()
                obj = artifact(name, digest, path.stat().st_size)
                client.fput_object(config.s3_bucket, f"{prefix}/{name}", str(path), content_type=obj["media_type"])
                objects[obj["uri"]] = obj
        manifest = result.model_copy(deep=True)
        manifest.publication = [s for s in manifest.publication if s.name != "s3"] + [
            StepResult(name="s3", blocking=config.persistence_required, details=details())
        ]
        data = manifest.model_dump_json(indent=2).encode()
        client.put_object(
            config.s3_bucket, f"{prefix}/run.json", io.BytesIO(data), len(data), content_type="application/json"
        )
        obj = artifact("run.json", hashlib.sha256(data).hexdigest(), len(data))
        objects[obj["uri"]] = obj
    except Exception as exc:
        raise S3UploadError(details()) from exc
    return details()
