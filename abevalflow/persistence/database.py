"""PostgreSQL connections through an optional TLS passthrough route."""

from __future__ import annotations

import os
import socket
import socketserver
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def database_connection(url: str, route_host: str | None = None) -> Iterator[str]:
    """Relay PostgreSQL wire traffic through verified HTTPS-port TLS when configured."""
    if not route_host:
        yield url
        return
    from sqlalchemy.engine import make_url

    database = make_url(url)
    if database.host not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("TLS route connections require a loopback database URL")
    context = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE"))

    class Relay(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                with socket.create_connection((route_host, 443), timeout=30) as raw:
                    with context.wrap_socket(raw, server_hostname=route_host) as upstream:
                        upstream.settimeout(None)

                        def copy(source, destination):
                            try:
                                while data := source.recv(65536):
                                    destination.sendall(data)
                            except OSError:
                                pass
                            finally:
                                try:
                                    destination.shutdown(socket.SHUT_WR)
                                except OSError:
                                    pass

                        outgoing = threading.Thread(target=copy, args=(self.request, upstream), daemon=True)
                        outgoing.start()
                        copy(upstream, self.request)
                        outgoing.join(timeout=5)
            except OSError:
                # Connection failures are reported by the database client without printing credentials.
                return

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Server(("127.0.0.1", 0), Relay) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            yield database.set(host="127.0.0.1", port=server.server_address[1]).render_as_string(hide_password=False)
        finally:
            server.shutdown()
            worker.join(timeout=5)
