from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import networkx as nx

from common.config import get_settings

settings = get_settings()


@dataclass
class TopologyEngine:
    """
    In-memory directed graph synced from /network/status/<ip> reports.
    """

    graph: nx.DiGraph

    @classmethod
    def create(cls) -> "TopologyEngine":
        return cls(graph=nx.DiGraph())

    def update_from_report(self, node_ip: str, report_data: Dict[str, Any]) -> None:
        # 1) node discovery + attributes
        node_status = report_data.get("node_status", {}) or {}
        is_overloaded = bool(node_status.get("is_overloaded", False))
        cpu_percent = float(node_status.get("cpu_percent", 0.0) or 0.0)
        self.graph.add_node(node_ip, overloaded=is_overloaded, cpu=cpu_percent)

        # 2) edge discovery + weight refresh
        links = report_data.get("links", {}) or {}
        for neighbor_ip, link_info in links.items():
            if not isinstance(link_info, dict):
                continue
            rtt = float(link_info.get("rtt_ms", 9999) or 9999)
            status = str(link_info.get("status", "DOWN") or "DOWN")
            bw = float(link_info.get("bw", settings.default_bw_mbps) or settings.default_bw_mbps)
            jitter = float(link_info.get("jitter", 0.0) or 0.0)
            loss = float(link_info.get("loss", 0.0) or 0.0)
            # Bound loss to [0,1] to avoid poisoning routing.
            if loss < 0.0:
                loss = 0.0
            if loss > 1.0:
                loss = 1.0

            if status == "UP":
                self.graph.add_edge(
                    node_ip,
                    neighbor_ip,
                    delay=rtt,
                    bw=bw,
                    jitter=jitter,
                    loss=loss,
                )
            else:
                if self.graph.has_edge(node_ip, neighbor_ip):
                    self.graph.remove_edge(node_ip, neighbor_ip)

    def prune_node(self, node_ip: str) -> None:
        if self.graph.has_node(node_ip):
            self.graph.remove_node(node_ip)

