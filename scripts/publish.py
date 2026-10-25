"""Publish AEH reports, run artifacts, and advisory review output to MinIO."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def _minio_insecure_tls() -> bool:
    """Whether to skip TLS certificate verification for MinIO connections.

    Opt-in via ``MINIO_INSECURE_SSL=1`` -- needed when the MinIO endpoint is
    only reachable through a Route/Ingress presenting a self-signed or
    internal-CA certificate (e.g. this cluster's default OpenShift router
    cert) that isn't in this process's trust store. Deliberately a separate
    flag from ``AEH_INSECURE_SSL`` (containers/agent-eval-harness/
    sitecustomize.py): that one patches httpx/httpx2/requests/aiohttp for
    judge HTTP calls and has no effect on the ``minio`` SDK, which builds
    its own ``urllib3.PoolManager`` directly (``cert_reqs='CERT_REQUIRED'``
    unless told otherwise) -- so disabling verification here requires an
    explicit ``cert_check=False`` on the ``Minio()`` client itself.
    """
    import os

    return os.environ.get("MINIO_INSECURE_SSL", "").strip().lower() in ("1", "true", "yes")


def _minio_client(endpoint: str, access_key: str, secret_key: str, secure: bool | None = None):
    """Build a ``Minio`` client, centralizing the scheme/cert_check wiring.

    ``secure`` is auto-detected from the endpoint scheme when not provided.
    ``cert_check`` is derived from ``_minio_insecure_tls()`` -- see its
    docstring for why this can't just piggyback on ``AEH_INSECURE_SSL``.
    """
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
    """Upload report.json/report.md plus Tier A security/quality scan output to MinIO.

    Returns the artifact prefix on success, None on failure.
    ``secure`` is auto-detected from the endpoint scheme when not provided.
    ``report_prefix`` overrides auto-generated prefix if provided.

    ``workspace_root``, when given, is also checked for
    ``skillmd-quality-scan.json`` -- aeh-skill-eval.yaml's skillmd-scan step
    writes that one file at the leg's workspace root rather than inside
    ``report_dir`` (see that Task's own REPORT_KEY INVARIANT note), so it
    can't be found via ``report_dir`` alone.

    The security-scan.json/.sarif (skill-scanner) and
    skillmd-security-scan.json/skillmd-quality-scan.json (harness-eval)
    files are this run's Tier A (security + SKILL.md quality) findings --
    the ONLY place those findings are persisted anywhere outside ephemeral
    Tekton pod logs. They are uploaded unconditionally whenever present, so
    a Tier A failure is still debuggable from MinIO even when it has no
    Tier B (LLM eval) results to sit alongside. (Previously these were
    skipped whenever ``report_prefix`` was passed, under the assumption a
    separate upstream "security-scan task" had already uploaded them
    directly -- that direct-upload step doesn't exist in the current
    single-Task aeh-skill-eval.yaml architecture, where security-scan/
    skillmd-scan only ever write to local disk, so skipping here meant
    these files were never uploaded at all.)
    """
    prefix = report_prefix or _build_artifact_prefix(submission_name, pipeline_run_id)

    client = _minio_client(endpoint, access_key, secret_key, secure=secure)

    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info("Created bucket: %s", bucket)

    uploaded = 0
    # Core report files (required)
    # Core AEH and scorecard report files
    core_files = ["report.json", "report.md", "scorecard.json", "certification.json", "skill-structure-validation.json"]
    for filename in core_files:
        filepath = report_dir / filename
        if not filepath.exists():
            continue

        object_name = f"{prefix}/{filename}"
        client.fput_object(bucket, object_name, str(filepath))
        logger.info("Uploaded %s -> s3://%s/%s", filepath, bucket, object_name)
        uploaded += 1

    # Tier A security/quality scan files (optional) -- stored in
    # security_scans/ subfolder. See the docstring above for why these are
    # now always uploaded when present, not gated on report_prefix.
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

    # Direct timestamp job dirs (single: results_dir == aeh-jobs)
    found.extend(_timestamp_jobs(results_dir, ""))

    # Nested aeh-jobs/ under _eval_tmp — flatten (no "aeh-jobs/" segment)
    nested = results_dir / "aeh-jobs"
    if nested.is_dir():
        found.extend(_timestamp_jobs(nested, ""))

    # Pairwise: aeh-control-jobs-* / aeh-treatment-jobs-* → control/ / treatment/
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

    # Allow passing a single job dir directly.
    if not found and any((results_dir / name).exists() for name in ("result.json", "job.log", "config.json")):
        found = [(results_dir.name, results_dir)]

    # De-dupe by resolved path while preserving order
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


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Publish evaluation artifacts")
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--submission-name", type=str, required=True)
    parser.add_argument("--pipeline-run-id", type=str, required=True)
    parser.add_argument("--results-dir", type=Path, default=None, help="Path to AEH results for debug artifact upload")
    parser.add_argument(
        "--eval-engine",
        type=str,
        default="aeh",
        choices=["aeh"],
        help="Evaluation engine used — determines debug artifact layout",
    )
    parser.add_argument(
        "--workspace-root", type=Path, default=None, help="Workspace root containing advisory review output"
    )
    parser.add_argument(
        "--minio-endpoint", type=str, default=None, help="MinIO endpoint (default: MINIO_ENDPOINT env var)"
    )
    parser.add_argument("--minio-bucket", type=str, default="ab-eval-reports")
    parser.add_argument(
        "--report-prefix", type=str, default="", help="Pre-computed MinIO prefix (skips generating new timestamp)"
    )
    args = parser.parse_args()

    import os

    minio_endpoint = args.minio_endpoint or os.environ.get("MINIO_ENDPOINT", "")
    minio_access_key = os.environ.get("MINIO_ACCESS_KEY", "")
    minio_secret_key = os.environ.get("MINIO_SECRET_KEY", "")

    # --- 1. Upload reports to MinIO ---
    if minio_endpoint and minio_access_key:
        prefix = upload_reports(
            report_dir=args.report_dir,
            submission_name=args.submission_name,
            pipeline_run_id=args.pipeline_run_id,
            endpoint=minio_endpoint,
            access_key=minio_access_key,
            secret_key=minio_secret_key,
            bucket=args.minio_bucket,
            report_prefix=args.report_prefix or None,
            workspace_root=args.workspace_root,
        )
        if prefix:
            logger.info("Reports uploaded to s3://%s/%s/", args.minio_bucket, prefix)
        else:
            logger.warning("No report files uploaded — continuing with debug artifacts")
            prefix = args.report_prefix or _build_artifact_prefix(
                args.submission_name,
                args.pipeline_run_id,
            )
        if args.results_dir:
            upload_aeh_debug_artifacts(
                results_dir=args.results_dir,
                prefix=prefix,
                endpoint=minio_endpoint,
                access_key=minio_access_key,
                secret_key=minio_secret_key,
                bucket=args.minio_bucket,
            )
        # AEH harness run dirs (summary.yaml, report.html, cases/, …)
        # live under reports/<skill>/ — upload for both single and pairwise.
        if prefix and args.eval_engine == "aeh":
            upload_aeh_run_artifacts(
                report_dir=args.report_dir,
                prefix=prefix,
                endpoint=minio_endpoint,
                access_key=minio_access_key,
                secret_key=minio_secret_key,
                bucket=args.minio_bucket,
                workspace_root=args.workspace_root,
            )
        if prefix and args.workspace_root:
            upload_review_artifact(
                workspace_root=args.workspace_root,
                submission_name=args.submission_name,
                prefix=prefix,
                endpoint=minio_endpoint,
                access_key=minio_access_key,
                secret_key=minio_secret_key,
                bucket=args.minio_bucket,
            )
    else:
        logger.warning("MinIO not configured — skipping report upload")

    return 0


if __name__ == "__main__":
    sys.exit(main())
