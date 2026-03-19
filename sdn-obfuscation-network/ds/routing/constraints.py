from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

import networkx as nx


@dataclass(frozen=True)
class PathQosMetrics:
    delay_ms: float
    jitter_ms: float
    loss_rate: float
    bottleneck_bw_mbps: float


@dataclass(frozen=True)
class ConstraintParams:
    min_hops: int
    max_hops: int
    max_delay_ms: float
    max_jitter_ms: float
    max_loss_rate: float
    min_bw_mbps: float


def filter_path_constraints(
    graph: nx.DiGraph,
    path: List[str],
    params: ConstraintParams,
) -> Tuple[bool, Dict[str, str], PathQosMetrics | None]:
    """
    Validate a candidate path using multi-QoS constraints with early pruning.

    Returns: (ok, reject_reason_counts, metrics)
    - reject_reason_counts: {reason: '1'} for the first violated constraint (for stats aggregation)
    """
    hop_count = len(path) - 1
    if hop_count < params.min_hops:
        return False, {"min_hops": "1"}, None
    if hop_count > params.max_hops:
        return False, {"max_hops": "1"}, None

    total_delay = 0.0
    total_jitter = 0.0
    success_rate = 1.0
    bottleneck_bw = float("inf")

    for i in range(len(path) - 1):
        u = path[i]
        v = path[i + 1]
        edge_data = graph.get_edge_data(u, v) or {}

        delay = float(edge_data.get("delay", float("inf")))
        jitter = float(edge_data.get("jitter", 0.0))
        loss = float(edge_data.get("loss", 0.0))
        bw = float(edge_data.get("bw", 0.0))

        if loss < 0.0:
            loss = 0.0
        if loss > 1.0:
            loss = 1.0

        total_delay += delay
        total_jitter += jitter
        success_rate *= (1.0 - loss)
        bottleneck_bw = min(bottleneck_bw, bw)

        # Early pruning
        if total_delay > params.max_delay_ms:
            return False, {"delay": "1"}, None
        if total_jitter > params.max_jitter_ms:
            return False, {"jitter": "1"}, None
        current_loss_rate = 1.0 - success_rate
        if current_loss_rate > params.max_loss_rate:
            return False, {"loss": "1"}, None

    if bottleneck_bw < params.min_bw_mbps:
        return False, {"bw": "1"}, None

    return True, {}, PathQosMetrics(
        delay_ms=total_delay,
        jitter_ms=total_jitter,
        loss_rate=1.0 - success_rate,
        bottleneck_bw_mbps=bottleneck_bw,
    )

