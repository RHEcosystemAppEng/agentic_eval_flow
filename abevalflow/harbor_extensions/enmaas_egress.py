"""Bounded per-attempt HTTPS access to the trusted ENMAAS gateway."""

import ipaddress
import socket
from datetime import UTC, datetime

from abevalflow.mcp_ci import LABEL, atomic_json

HOST = "api.enmaas.devshift.net"


def allow_enmaas(cluster, uid, attempt, evidence_dir):
    """Resolve only the trusted gateway; fail closed on non-public answers."""
    addresses = sorted({item[4][0] for item in socket.getaddrinfo(HOST, 443, type=socket.SOCK_STREAM)})
    if not addresses or any(
        not ipaddress.ip_address(value).is_global
        or ipaddress.ip_address(value).is_multicast
        or ipaddress.ip_address(value).is_reserved
        for value in addresses
    ):
        raise ValueError("ENMAAS must resolve to public IP addresses")
    policy = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": "enmaas-" + uid[:24] + "-" + attempt,
            "labels": {LABEL: uid, "abevalflow.io/mcp-attempt": attempt},
        },
        "spec": {
            "podSelector": {"matchLabels": {LABEL: uid, "abevalflow.io/mcp-role": "harbor-client"}},
            "policyTypes": ["Egress"],
            "egress": [
                {
                    "to": [{"ipBlock": {"cidr": value + ("/128" if ":" in value else "/32")}} for value in addresses],
                    "ports": [{"protocol": "TCP", "port": 443}],
                }
            ],
        },
    }
    atomic_json(
        evidence_dir / "enmaas-egress.json",
        {
            "hostname": HOST,
            "resolved_at": datetime.now(UTC).isoformat(),
            "addresses": addresses,
            "policy": policy,
            "boundary": "IP/port allowlist; TLS validates hostname. Shared IPs are not FQDN isolation.",
        },
    )
    cluster.create(policy)
