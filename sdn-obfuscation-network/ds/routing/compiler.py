from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class CompiledTunnel:
    tunnel_id: int
    first_hop_ip: str
    first_hop_port: int
    node_ports: Dict[str, int]
    # node_ip -> rule dict (JSON-serializable)
    rules: List[Tuple[str, dict]]


def compile_portmapped_tunnel(
    *,
    path: List[str],
    tunnel_id: int,
    target_final_port: int,
    port_min: int,
    port_max: int,
    ttl_seconds: int | None,
) -> CompiledTunnel:
    """
    Compile a multi-hop path into per-node tinyPortMapper rules.

    Path format: [smr_endpoint, sor1, sor2, ..., sar_ip]
    Rules are issued for each intermediate SOR node (path[1:-1]).
    """
    if len(path) < 3:
        raise ValueError("path too short for obfuscation (need >= 3 nodes)")

    now = time.time()
    node_ports: Dict[str, int] = {}
    for node in path[1:-1]:
        node_ports[node] = random.randint(port_min, port_max)

    rules: List[Tuple[str, dict]] = []
    for i in range(1, len(path) - 1):
        current_node = path[i]
        next_node = path[i + 1]

        if i == len(path) - 2:
            rp = target_final_port
        else:
            rp = node_ports[next_node]

        rule_data = {
            "tunnel_id": tunnel_id,
            "lp": node_ports[current_node],
            "rip": next_node,
            "rp": rp,
            "created_at": now,
            "ttl_seconds": ttl_seconds,
        }
        rules.append((current_node, rule_data))

    first_hop_ip = path[1]
    first_hop_port = node_ports[first_hop_ip]
    return CompiledTunnel(
        tunnel_id=tunnel_id,
        first_hop_ip=first_hop_ip,
        first_hop_port=first_hop_port,
        node_ports=node_ports,
        rules=rules,
    )

