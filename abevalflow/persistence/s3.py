"""Persistence s3 services."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _minio_insecure_tls() -> bool:
    """Read the explicit MinIO TLS verification override."""
    import os

    return os.environ.get("MINIO_INSECURE_SSL", "").strip().lower() in ("1", "true", "yes")


def _minio_client(endpoint: str, access_key: str, secret_key: str, secure: bool | None = None):
    """Create a MinIO client with endpoint-derived transport and explicit certificate verification."""
    from minio import Minio

    parsed = urlparse(endpoint)
    host = parsed.netloc or parsed.path
    if secure is None:
        secure = parsed.scheme == "https"

    return Minio(
        host,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
        cert_check=not _minio_insecure_tls(),
    )


def _build_artifact_prefix(
    submission_name: str,
    pipeline_run_id: str,
) -> str:
    """Build the MinIO object prefix: YYYYMMDD_hhmmss_<name>_<run-id>."""
    datestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"{datestamp}_{submission_name}_{pipeline_run_id}"


def upload_reports(
    report_dir: Path,
    submission_name: str,
    pipeline_run_id: str,
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str = "ab-eval-reports",
    secure: bool | None = None,
    report_prefix: str | None = None,
    workspace_root: Path | None = None,
) -> str | None:
    """Upload reports and available scanner evidence to S3. Return the object prefix or None when empty."""
    prefix = report_prefix or _build_artifact_prefix(submission_name, pipeline_run_id)

    client = _minio_client(endpoint, access_key, secret_key, secure=secure)

    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info("Created bucket: %s", bucket)

    uploaded = 0

    core_files = ["report.json", "report.md", "scorecard.json", "certification.json", "skill-structure-validation.json"]
    for filename in core_files:
        filepath = report_dir / filename
        if not filepath.exists():
            continue

        object_name = f"{prefix}/{filename}"
        client.fput_object(bucket, object_name, str(filepath))
        logger.info("Uploaded %s -> s3://%s/%s", filepath, bucket, object_name)
        uploaded += 1

    scan_candidates = [
        report_dir / "security-scan.json",
        report_dir / "security-scan.sarif",
        report_dir / "skillmd-security-scan.json",
    ]
    if workspace_root is not None:
        scan_candidates.append(workspace_root / "skillmd-quality-scan.json")
    for filepath in scan_candidates:
        if filepath.exists():
            object_name = f"{prefix}/security_scans/{filepath.name}"
            client.fput_object(bucket, object_name, str(filepath))
            logger.info("Uploaded %s -> s3://%s/%s", filepath, bucket, object_name)
            uploaded += 1

    if uploaded == 0:
        logger.warning("No report files found in %s", report_dir)
        return None

    return prefix


def _discover_aeh_harbor_job_dirs(results_dir: Path) -> list[tuple[str, Path]]:
    """Discover Harbor job dirs for AEH single and pairwise layouts.

    Returns ``(object_prefix, job_dir)`` pairs where ``object_prefix`` is the
    path under ``debug/harbor/`` (no trailing slash).

    Supported layouts (uploaded under ``debug/harbor/``):
    - ``aeh-jobs/YYYY-MM-DD__…`` → ``{timestamp}/…`` (single)
    - ``_eval_tmp/aeh-jobs/YYYY-MM-DD__…`` → ``{timestamp}/…`` (no aeh-jobs segment)
    - ``_eval_tmp/aeh-*-jobs-*/YYYY-MM-DD__…`` → ``control|treatment/{timestamp}/…``
    - a single job dir passed directly (contains ``result.json`` / ``job.log``)
    """

    def _timestamp_jobs(parent: Path, prefix: str) -> list[tuple[str, Path]]:
        jobs = sorted(
            (d for d in parent.iterdir() if d.is_dir() and d.name.startswith("20")),
            key=lambda d: d.stat().st_mtime,
        )
        return [(f"{prefix}/{d.name}" if prefix else d.name, d) for d in jobs]

    found: list[tuple[str, Path]] = []

    found.extend(_timestamp_jobs(results_dir, ""))

    nested = results_dir / "aeh-jobs"
    if nested.is_dir():
        found.extend(_timestamp_jobs(nested, ""))

    for sibling in sorted(results_dir.iterdir()):
        if not sibling.is_dir():
            continue
        name = sibling.name
        if name.startswith("aeh-") and "-jobs" in name and name != "aeh-jobs":
            if "control" in name:
                label = "control"
            elif "treatment" in name:
                label = "treatment"
            else:
                label = name
            found.extend(_timestamp_jobs(sibling, label))

    if not found and any((results_dir / name).exists() for name in ("result.json", "job.log", "config.json")):
        found = [(results_dir.name, results_dir)]

    seen: set[Path] = set()
    unique: list[tuple[str, Path]] = []
    for prefix, job in found:
        key = job.resolve()
        if key in seen:
            continue
        seen.add(key)
        unique.append((prefix, job))
    return unique


def upload_aeh_debug_artifacts(
    results_dir: Path,
    prefix: str,
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str = "ab-eval-reports",
    secure: bool | None = None,
) -> int:
    """Upload AEH Harbor job trees as individual files under ``debug/harbor/``.

    ``results_dir`` is typically ``_eval_tmp`` (pairwise) or ``_eval_tmp/aeh-jobs``
    (single). Each file is uploaded unpacked (not tarred) to::

        {prefix}/debug/harbor/{job_path}/{relative_path}

    For pairwise, ``job_path`` includes the control/treatment jobs dir name so
    both variants land under the same report prefix.
    """
    if not results_dir.is_dir():
        logger.warning("AEH jobs dir does not exist, skipping debug upload: %s", results_dir)
        return 0

    client = _minio_client(endpoint, access_key, secret_key, secure=secure)

    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)

    job_dirs = _discover_aeh_harbor_job_dirs(results_dir)
    if not job_dirs:
        logger.warning("No AEH Harbor job directories under %s", results_dir)
        return 0

    uploaded = 0
    for job_prefix, job_dir in job_dirs:
        for fpath in sorted(job_dir.rglob("*")):
            if not fpath.is_file():
                continue
            rel = fpath.relative_to(job_dir)
            object_name = f"{prefix}/debug/harbor/{job_prefix}/{rel.as_posix()}"
            try:
                client.fput_object(bucket, object_name, str(fpath))
                uploaded += 1
            except Exception as exc:
                logger.warning("Failed to upload %s: %s", fpath, exc)

    logger.info(
        "Uploaded %d AEH debug artifact files to s3://%s/%s/debug/harbor/",
        uploaded,
        bucket,
        prefix,
    )
    return uploaded


def _discover_aeh_run_dirs(
    report_dir: Path,
    workspace_root: Path | None = None,
) -> list[Path]:
    """Find AEH harness run directories (summary.yaml / run_result.json).

    Layout (both single and pairwise)::

        reports/<skill>/<run-id>/summary.yaml
        reports/<skill>/control-<run-id>/…
        reports/<skill>/treatment-<run-id>/…

    ``report_dir`` is typically ``reports/<submission-name>``. When the skill
    name differs, also scan ``workspace_root/reports/*``.
    """
    roots: list[Path] = []
    if report_dir.is_dir():
        roots.append(report_dir)
    if workspace_root is not None:
        reports_root = workspace_root / "reports"
        if reports_root.is_dir():
            for child in sorted(reports_root.iterdir()):
                if child.is_dir() and child.resolve() not in {r.resolve() for r in roots}:
                    roots.append(child)

    run_dirs: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if not ((child / "summary.yaml").is_file() or (child / "run_result.json").is_file()):
                continue
            key = child.resolve()
            if key in seen:
                continue
            seen.add(key)
            run_dirs.append(child)
    return run_dirs


def upload_aeh_run_artifacts(
    report_dir: Path,
    prefix: str,
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str = "ab-eval-reports",
    secure: bool | None = None,
    workspace_root: Path | None = None,
) -> int:
    """Upload AEH harness run trees under ``debug/aeh/``.

    Uploads each run directory (``summary.yaml``, ``report.html``,
    ``run_result.json``, ``cases/``, …) to::

        {prefix}/debug/aeh/{run_dir_name}/{relative_path}

    Works for single (one run dir) and pairwise (control-*/treatment-*).
    """
    run_dirs = _discover_aeh_run_dirs(report_dir, workspace_root=workspace_root)
    if not run_dirs:
        logger.warning(
            "No AEH run directories under %s (summary.yaml/run_result.json) — skipping debug/aeh/",
            report_dir,
        )
        return 0

    client = _minio_client(endpoint, access_key, secret_key, secure=secure)

    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)

    uploaded = 0
    for run_dir in run_dirs:
        for fpath in sorted(run_dir.rglob("*")):
            if not fpath.is_file():
                continue
            rel = fpath.relative_to(run_dir)
            object_name = f"{prefix}/debug/aeh/{run_dir.name}/{rel.as_posix()}"
            try:
                client.fput_object(bucket, object_name, str(fpath))
                uploaded += 1
            except Exception as exc:
                logger.warning("Failed to upload %s: %s", fpath, exc)

    logger.info(
        "Uploaded %d AEH run artifact files to s3://%s/%s/debug/aeh/ (%d run dir(s))",
        uploaded,
        bucket,
        prefix,
        len(run_dirs),
    )
    return uploaded


def upload_review_artifact(
    workspace_root: Path,
    submission_name: str,
    prefix: str,
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str = "ab-eval-reports",
    secure: bool | None = None,
) -> int:
    """Upload advisory review output using its existing MinIO object key."""
    client = _minio_client(endpoint, access_key, secret_key, secure=secure)

    uploaded = 0
    ai_review_path = workspace_root / "_ai_review.json"
    if ai_review_path.is_file():
        object_name = f"{prefix}/scaffolded/ai_review.json"
        try:
            client.fput_object(bucket, object_name, str(ai_review_path))
            uploaded += 1
        except Exception as exc:
            logger.warning("Failed to upload AI review: %s", exc)

    return uploaded


def publish_artifacts(
    report_dir: Path,
    submission_name: str,
    pipeline_run_id: str,
    minio_endpoint: str,
    minio_access_key: str,
    minio_secret_key: str,
    results_dir: Path | None = None,
    eval_engine: str = "aeh",
    workspace_root: Path | None = None,
    minio_bucket: str = "ab-eval-reports",
    report_prefix: str = "",
) -> int:
    """Publish the compatibility artifact layout using explicit destination configuration."""
    if minio_endpoint and minio_access_key:
        prefix = upload_reports(
            report_dir=report_dir,
            submission_name=submission_name,
            pipeline_run_id=pipeline_run_id,
            endpoint=minio_endpoint,
            access_key=minio_access_key,
            secret_key=minio_secret_key,
            bucket=minio_bucket,
            report_prefix=report_prefix or None,
            workspace_root=workspace_root,
        )
        if prefix:
            logger.info("Reports uploaded to s3://%s/%s/", minio_bucket, prefix)
        else:
            logger.warning("No report files uploaded — continuing with debug artifacts")
            prefix = report_prefix or _build_artifact_prefix(
                submission_name,
                pipeline_run_id,
            )
        if results_dir:
            upload_aeh_debug_artifacts(
                results_dir=results_dir,
                prefix=prefix,
                endpoint=minio_endpoint,
                access_key=minio_access_key,
                secret_key=minio_secret_key,
                bucket=minio_bucket,
            )
        # AEH harness run dirs (summary.yaml, report.html, cases/, …)
        # live under reports/<skill>/ — upload for both single and pairwise.
        if prefix and eval_engine == "aeh":
            upload_aeh_run_artifacts(
                report_dir=report_dir,
                prefix=prefix,
                endpoint=minio_endpoint,
                access_key=minio_access_key,
                secret_key=minio_secret_key,
                bucket=minio_bucket,
                workspace_root=workspace_root,
            )
        if prefix and workspace_root:
            upload_review_artifact(
                workspace_root=workspace_root,
                submission_name=submission_name,
                prefix=prefix,
                endpoint=minio_endpoint,
                access_key=minio_access_key,
                secret_key=minio_secret_key,
                bucket=minio_bucket,
            )
    else:
        logger.warning("MinIO not configured — skipping report upload")

    return 0
