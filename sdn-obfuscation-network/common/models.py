from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class NodeStatus:
    cpu_percent: float
    mem_percent: float
    is_overloaded: bool


@dataclass(frozen=True)
class LinkStatus:
    rtt_ms: float
    status: str  # "UP" | "DOWN"
    bw: Optional[float] = None
    jitter: Optional[float] = None
    loss: Optional[float] = None


@dataclass(frozen=True)
class TelemetryReport:
    timestamp: float
    node_status: NodeStatus
    links: Dict[str, LinkStatus]  # neighbor_ip -> LinkStatus

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "node_status": {
                "cpu_percent": self.node_status.cpu_percent,
                "mem_percent": self.node_status.mem_percent,
                "is_overloaded": self.node_status.is_overloaded,
            },
            "links": {
                ip: {
                    "rtt_ms": ls.rtt_ms,
                    "status": ls.status,
                    **({} if ls.bw is None else {"bw": ls.bw}),
                    **({} if ls.jitter is None else {"jitter": ls.jitter}),
                    **({} if ls.loss is None else {"loss": ls.loss}),
                }
                for ip, ls in self.links.items()
            },
        }


@dataclass(frozen=True)
class TunnelRule:
    tunnel_id: int
    lp: int
    rip: str
    rp: int
    ttl_seconds: Optional[int] = None
    created_at: Optional[float] = None

