#!/usr/bin/env bash
# Download verified pod-log archives using the Python MinIO client.
# No commands run inside the MinIO server container.
set -euo pipefail
exec python3 "$(dirname "$0")/fetch_run_logs.py" "$@"
