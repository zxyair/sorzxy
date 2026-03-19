from __future__ import annotations


NETWORK_PREFIX = "/network"


def status_key(node_ip: str) -> str:
    return f"{NETWORK_PREFIX}/status/{node_ip}"


def status_prefix() -> str:
    return f"{NETWORK_PREFIX}/status/"


def neighbor_config_key(node_ip: str) -> str:
    return f"{NETWORK_PREFIX}/config/{node_ip}/neighbors"


def rules_prefix(node_ip: str) -> str:
    return f"{NETWORK_PREFIX}/rules/{node_ip}/"


def rule_key(node_ip: str, tunnel_id: int) -> str:
    return f"{NETWORK_PREFIX}/rules/{node_ip}/tunnel_{tunnel_id}"


def sar_key(sar_ip: str) -> str:
    return f"{NETWORK_PREFIX}/sar/{sar_ip}"


def sar_prefix() -> str:
    return f"{NETWORK_PREFIX}/sar/"

