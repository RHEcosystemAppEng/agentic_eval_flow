"""Exercise the packaged OSAC mock over real HTTP, including uncertain writes."""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def test_packaged_osac_uncertain_create_http():
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    root = Path(__file__).resolve().parents[1] / "containers/mcp-mocks/osac-tenant-vm"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}/mcp"
    code = f"""from evals.tenant_vm.mock_server.server import Audit,build_fixture,create_server
f=build_fixture("CREATE-UNCERTAIN")
s=create_server(f,Audit("CREATE-UNCERTAIN",None,f),{url!r})
s.run(transport="streamable-http",host="127.0.0.1",port={port})
"""
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", code],
        env={**os.environ, "PYTHONPATH": str(root)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 15
        while True:
            assert process.poll() is None, "packaged mock failed tool registration/startup"
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                if time.monotonic() > deadline:
                    raise TimeoutError("mock did not start") from None
                time.sleep(0.1)

        async def exercise():
            async with streamable_http_client(url) as transport:
                async with ClientSession(transport[0], transport[1]) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert {t.name for t in tools.tools} == {
                        "check_connection",
                        "get_resource",
                        "list_resources",
                        "create_compute_instance",
                        "delete_compute_instance",
                    }
                    connected = await session.call_tool("check_connection", {})
                    assert not connected.is_error
                    catalog = await session.call_tool(
                        "get_resource",
                        {"resource_type": "compute_instance_catalog_item", "id": "catalog-linux-001"},
                    )
                    assert not catalog.is_error
                    created = await session.call_tool(
                        "create_compute_instance", {"catalog_item": "catalog-linux-001", "name": "uncertain-vm-01"}
                    )
                    assert created.is_error and "DeadlineExceeded" in created.model_dump_json()
                    listed = await session.call_tool("list_resources", {"resource_type": "compute_instance"})
                    assert not listed.is_error
                    data = json.loads(listed.content[0].text)
                    assert any(
                        v["id"] == "vm-uncertain-created-001" and v["state"] == "COMPUTE_INSTANCE_STATE_STARTING"
                        for v in data["items"]
                    )

        asyncio.run(exercise())
    finally:
        process.terminate()
        process.wait(timeout=10)
