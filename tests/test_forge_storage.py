from types import SimpleNamespace

import pytest


def test_upload_requires_readback_of_all_bytes(tmp_path):
    from abevalflow.artifact_storage import upload_verified

    path = tmp_path / "brief.json"
    path.write_text('{"evidenceId":"test"}')

    class Client:
        def fput_object(self, *args, **kwargs):
            pass

        def get_object(self, *args):
            return SimpleNamespace(stream=lambda n: iter([b"wrong"]), close=lambda: None, release_conn=lambda: None)

    with pytest.raises(RuntimeError, match="read-back"):
        upload_verified(Client(), "bucket", "key", path)


def test_upload_failure_propagates(tmp_path):
    from abevalflow.artifact_storage import upload_verified

    path = tmp_path / "file"
    path.write_text("data")

    class Client:
        def fput_object(self, *args, **kwargs):
            raise OSError("storage unavailable")

    with pytest.raises(OSError):
        upload_verified(Client(), "bucket", "key", path)
