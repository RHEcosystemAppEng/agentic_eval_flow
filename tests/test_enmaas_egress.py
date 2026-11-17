import json
from unittest.mock import Mock

import pytest

from abevalflow.harbor_extensions.enmaas_egress import allow_enmaas


def test_trusted_gateway_policy_and_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda host, port, **kwargs: (
            [
                (None, None, None, None, ("32.190.33.3", 443)),
            ]
            if (host, port) == ("api.enmaas.devshift.net", 443)
            else []
        ),
    )
    cluster = Mock()
    allow_enmaas(cluster, "runuid", "control", tmp_path)
    policy = cluster.create.call_args.args[0]
    assert policy["spec"]["podSelector"]["matchLabels"] == {
        "abevalflow.io/mcp-run": "runuid",
        "abevalflow.io/mcp-role": "harbor-client",
    }
    assert policy["spec"]["egress"] == [
        {"to": [{"ipBlock": {"cidr": "32.190.33.3/32"}}], "ports": [{"protocol": "TCP", "port": 443}]}
    ]
    assert json.loads((tmp_path / "enmaas-egress.json").read_text())["policy"] == policy


@pytest.mark.parametrize(
    "addresses", [[], ["127.0.0.1"], ["169.254.169.254"], ["224.0.0.1"], ["ff02::1"], ["32.190.33.3", "10.0.0.1"]]
)
def test_non_public_or_empty_dns_fails_closed(tmp_path, monkeypatch, addresses):
    monkeypatch.setattr(
        "socket.getaddrinfo", lambda *a, **kw: [(None, None, None, None, (value, 443)) for value in addresses]
    )
    cluster = Mock()
    with pytest.raises(ValueError):
        allow_enmaas(cluster, "uid", "control", tmp_path)
    cluster.create.assert_not_called()
