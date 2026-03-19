from __future__ import annotations

import json
import threading
from typing import Any, Dict

import etcd3

from common.etcd_keys import status_prefix
from ds.topology_engine import TopologyEngine


def _node_ip_from_status_key(key_bytes: bytes) -> str:
    key = key_bytes.decode("utf-8")
    return key.split("/")[-1]


def start_telemetry_watcher(
    *,
    etcd_client: etcd3.Etcd3Client,
    topo_engine: TopologyEngine,
    neighbor_manager: Any,
    node_lifecycle: Dict[str, int],
) -> threading.Thread:
    """
    Watch /network/status/ and:
    - update topology on PutEvent
    - prune topology on DeleteEvent (TTL expiry / node offline)
    - drive neighbor assignment state machine (0/1/2)
    """

    def _run() -> None:
        print("[*] 👁️ 上帝之眼 (遥测引擎) 已启动，实时同步全网节点与链路状态...")
        events_iterator, _ = etcd_client.watch_prefix(status_prefix())

        for event in events_iterator:
            event_type = type(event).__name__

            if event_type == "PutEvent":
                node_ip = _node_ip_from_status_key(event.key)
                try:
                    report_data = json.loads(event.value.decode("utf-8"))
                except Exception:
                    continue

                current_state = node_lifecycle.get(node_ip, 0)

                try:
                    if current_state == 0:
                        neighbor_manager.initial_blind_assignment(node_ip)
                        node_lifecycle[node_ip] = 1
                    elif current_state == 1:
                        reported_links = report_data.get("links", {}) or {}
                        expected_min = min(
                            neighbor_manager.k,
                            len(neighbor_manager.get_candidate_pool(node_ip)),
                        )
                        if len(reported_links) >= expected_min:
                            neighbor_manager.optimized_final_assignment(node_ip, reported_links)
                            node_lifecycle[node_ip] = 2
                    elif current_state == 2:
                        reported_links = report_data.get("links", {}) or {}
                        if len(reported_links) < neighbor_manager.k:
                            all_existing = neighbor_manager.get_candidate_pool(node_ip)
                            if len(all_existing) >= neighbor_manager.k:
                                print(
                                    f"[拓扑自愈] 💡 节点 {node_ip} 邻居数不足(仅 {len(reported_links)} 个)，重新激活发现引擎！"
                                )
                                neighbor_manager.initial_blind_assignment(node_ip)
                                node_lifecycle[node_ip] = 1
                except Exception:
                    # Keep watcher alive even if neighbor logic fails.
                    pass

                try:
                    topo_engine.update_from_report(node_ip, report_data)
                except Exception:
                    pass

            elif event_type == "DeleteEvent":
                node_ip = _node_ip_from_status_key(event.key)
                topo_engine.prune_node(node_ip)
                node_lifecycle.pop(node_ip, None)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t

