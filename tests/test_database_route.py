"""Verified TLS route relay lifetime and database URL handling."""

import socket
import socketserver
import threading
from unittest.mock import MagicMock, patch

import pytest

from abevalflow.persistence.database import database_connection


def test_direct_database_connection_does_not_start_a_tunnel():
    with patch("ssl.create_default_context") as tls:
        with database_connection("postgresql://user:secret@database/db") as url:
            assert url == "postgresql://user:secret@database/db"
    tls.assert_not_called()


def test_route_rejects_a_non_loopback_database_url():
    with pytest.raises(ValueError, match="loopback"):
        with database_connection("postgresql://user:secret@database/db", "route.example"):
            pass


def test_route_relays_bytes_and_closes_its_listener(monkeypatch):
    from sqlalchemy.engine import make_url

    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            while data := self.request.recv(65536):
                self.request.sendall(data)

    context = MagicMock()
    context.wrap_socket.side_effect = lambda stream, **kwargs: stream
    original_connect = socket.create_connection
    monkeypatch.setenv("SSL_CERT_FILE", "/trusted/ca.pem")
    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Echo) as echo:
        worker = threading.Thread(target=echo.serve_forever, daemon=True)
        worker.start()

        def connect(address, *args, **kwargs):
            if address == ("route.example", 443):
                address = echo.server_address
            return original_connect(address, *args, **kwargs)

        try:
            with (
                patch("ssl.create_default_context", return_value=context) as tls,
                patch("socket.create_connection", side_effect=connect),
            ):
                with database_connection("postgresql://user:secret@localhost:5432/db", "route.example") as forwarded:
                    parsed = make_url(forwarded)
                    assert parsed.password == "secret"
                    assert parsed.port != 5432
                    endpoint = (parsed.host, parsed.port)
                    with original_connect(endpoint, timeout=2) as client:
                        client.sendall(b"postgres-wire-protocol")
                        assert client.recv(1024) == b"postgres-wire-protocol"
                tls.assert_called_once_with(cafile="/trusted/ca.pem")
                assert context.wrap_socket.call_args.kwargs["server_hostname"] == "route.example"
                with pytest.raises(OSError):
                    original_connect(endpoint, timeout=1)
        finally:
            echo.shutdown()
            worker.join(timeout=5)
