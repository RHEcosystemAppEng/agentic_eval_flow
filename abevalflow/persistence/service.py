"""Independent, explicit publication destinations for completed and partial runs."""

from __future__ import annotations

import io

from abevalflow.config.runtime import RuntimeConfig
from abevalflow.results.run import RunResult, StepResult, write_json


def publish(config: RuntimeConfig, result: RunResult) -> list[StepResult]:
    """Attempt every selected destination and retain each outcome independently."""
    outcomes = []
    for destination in config.persist:
        try:
            if destination == "postgres":
                from abevalflow.persistence.postgres import store_snapshot

                if not config.database_url:
                    raise ValueError("Postgres requires ABEVAL_DATABASE_URL or DATABASE_URL")
                from abevalflow.persistence.database import database_connection

                with database_connection(config.database_url.get_secret_value(), config.database_tls_route_host) as url:
                    store_snapshot(config.output / "reports", url, config.run_id)
                details = {}
            elif destination == "s3":
                details = publish_s3(config, result)
            elif destination == "compass":
                from abevalflow.persistence.compass import publish_scorecard
                from abevalflow.scorecard import Scorecard

                path = config.output / "reports/scorecard.json"
                scorecard = Scorecard.model_validate_json(path.read_text())
                if not scorecard.policy.push_facts:
                    raise ValueError("Compass requires gate_policy.push_facts in submission metadata")
                if config.metadata and config.metadata.gate_policy and config.metadata.gate_policy.push_facts:
                    scorecard.policy.push_facts = config.metadata.gate_policy.push_facts.model_copy(deep=True)
                if config.compass_token:
                    scorecard.policy.push_facts.bearer_token = config.compass_token.get_secret_value()
                success = publish_scorecard(scorecard)
                scorecard.policy.push_facts.bearer_token = "${COMPASS_API_TOKEN}"
                write_json(path, scorecard.model_dump(mode="json"))
                if not success:
                    raise RuntimeError("One or more Compass facts could not be published")
                details = {}
            else:
                raise ValueError(f"Unsupported persistence destination: {destination}")
            outcomes.append(StepResult(name=destination, blocking=config.persistence_required, details=details))
        except Exception as exc:
            # SDK exception text can include connection credentials or request headers.
            outcomes.append(
                StepResult(
                    name=destination,
                    blocking=config.persistence_required,
                    status="error",
                    verdict="unavailable",
                    reason=f"{destination} publication failed ({type(exc).__name__})",
                )
            )
    return outcomes


def publish_s3(config: RuntimeConfig, result: RunResult) -> dict:
    """Upload all report artifacts and the run manifest under a stable retry-safe prefix."""
    from urllib.parse import urlparse

    from minio import Minio

    if not (config.s3_endpoint and config.s3_access_key and config.s3_secret_key):
        raise ValueError("S3 requires endpoint, access key, and secret key")
    parsed = urlparse(config.s3_endpoint if "://" in config.s3_endpoint else f"https://{config.s3_endpoint}")
    client = Minio(
        parsed.netloc,
        access_key=config.s3_access_key.get_secret_value(),
        secret_key=config.s3_secret_key.get_secret_value(),
        secure=parsed.scheme == "https",
        cert_check=config.s3_verify_tls,
    )
    if not client.bucket_exists(config.s3_bucket):
        client.make_bucket(config.s3_bucket)
    prefix = f"{config.name}/{config.run_id}"
    reports = config.output / "reports"
    count = 0
    for path in sorted(reports.rglob("*")):
        if path.is_file():
            client.fput_object(config.s3_bucket, f"{prefix}/reports/{path.relative_to(reports)}", str(path))
            count += 1
    data = result.model_dump_json(indent=2).encode()
    client.put_object(
        config.s3_bucket, f"{prefix}/run.json", io.BytesIO(data), len(data), content_type="application/json"
    )
    return {"uri": f"s3://{config.s3_bucket}/{prefix}/", "artifacts": count + 1}
