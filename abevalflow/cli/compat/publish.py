"""Command interface for publish."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from abevalflow.persistence.s3 import publish_artifacts


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
    return publish_artifacts(
        report_dir=args.report_dir,
        submission_name=args.submission_name,
        pipeline_run_id=args.pipeline_run_id,
        minio_endpoint=minio_endpoint,
        minio_access_key=minio_access_key,
        minio_secret_key=minio_secret_key,
        results_dir=args.results_dir,
        eval_engine=args.eval_engine,
        workspace_root=args.workspace_root,
        minio_bucket=args.minio_bucket,
        report_prefix=args.report_prefix,
    )
