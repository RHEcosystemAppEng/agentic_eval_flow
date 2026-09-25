"""Content-verified artifact upload for callers requiring durable evidence."""

import hashlib


def upload_verified(client, bucket, key, path):
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    client.fput_object(bucket, key, str(path), metadata={"sha256": expected})
    response = client.get_object(bucket, key)
    try:
        actual = hashlib.sha256()
        for chunk in response.stream(1024 * 1024):
            actual.update(chunk)
    finally:
        response.close()
        response.release_conn()
    if actual.hexdigest() != expected:
        raise RuntimeError("artifact read-back hash mismatch: " + key)
    return {"key": key, "sha256": expected, "size": path.stat().st_size}
