"""Populate and periodically refresh the OpenShell CLI OIDC token cache."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path


def fetch_token(issuer: str, client_id: str, client_secret: str) -> dict:
    # Match the existing Task's curl transport, including its CA handling.
    result = subprocess.run(
        [
            "curl",
            "-ksSf",
            "--max-time",
            "20",
            "-u",
            f"{client_id}:{client_secret}",
            "-d",
            "grant_type=client_credentials",
            f"{issuer.rstrip('/')}/protocol/openid-connect/token",
        ],
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def write_cache(path: Path, payload: dict, issuer: str, client_id: str) -> None:
    cache = {
        "access_token": payload["access_token"],
        "expires_at": int(time.time()) + int(payload.get("expires_in", 300)),
        "issuer": issuer,
        "client_id": client_id,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".oidc-token-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(cache, stream)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def refresh(path: Path, issuer: str, client_id: str, client_secret: str) -> None:
    write_cache(path, fetch_token(issuer, client_id, client_secret), issuer, client_id)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--interval", type=int, default=0, help="Seconds between refreshes; zero writes once")
    args = parser.parse_args()
    if args.interval < 0:
        parser.error("--interval must be nonnegative")

    issuer = os.environ["OPENSHELL_OIDC_ISSUER"]
    client_id = os.environ["OPENSHELL_OIDC_CLIENT_ID"]
    client_secret = os.environ["OPENSHELL_OIDC_CLIENT_SECRET"]
    if not args.interval:
        refresh(args.cache, issuer, client_id, client_secret)
        return

    while True:
        time.sleep(args.interval)
        try:
            refresh(args.cache, issuer, client_id, client_secret)
        except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
            print(f"WARN: OpenShell OIDC token refresh failed ({type(exc).__name__}); retrying", flush=True)


if __name__ == "__main__":
    main()
