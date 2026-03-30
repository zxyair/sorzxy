from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict

import etcd3

from common.config import get_settings
from common.etcd_keys import status_key, status_prefix
from ds.topology_engine import TopologyEngine

settings = get_settings()


def _node_ip_from_status_key(key_bytes: bytes) -> str:
    key = key_bytes.decode("utf-8")
    return key.split("/")[-1]

def _report_ts_seconds(report_data: Dict[str, Any]) -> float:
    ts = report_data.get("timestamp")
    now = time.time()
    if ts is None:
        return now
    try:
        tsf = float(ts)
    except Exception:
        return now
    # Defensive: if milliseconds are reported, normalize to seconds.
    if tsf > 1e11:
        return tsf / 1000.0
    return tsf


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
    last_seen_map: Dict[str, float] = {}
    state_lock = threading.Lock()
    offline_timeout_s = max(1.0, float(settings.sor_offline_timeout_s))

    def _timeout_pruner() -> None:
        while True:
            time.sleep(1.0)
            now = time.time()
            stale_nodes = []
            with state_lock:
                for ip, last_seen in list(last_seen_map.items()):
                    if now - last_seen > offline_timeout_s:
                        stale_nodes.append((ip, last_seen))
                        last_seen_map.pop(ip, None)
                        node_lifecycle.pop(ip, None)
            for ip, last_seen in stale_nodes:
                try:
                    topo_engine.prune_node(ip)
                    # Double safety: proactively remove stale status key.
                    etcd_client.delete(status_key(ip))
                    print(
                        "[DS 拓扑管理] ⏱️ node timeout -> pruned+deleted | "
                        f"node={ip} last_seen={last_seen:.3f} "
                        f"now={now:.3f} timeout_s={offline_timeout_s:.1f}"
                    )
                except Exception:
                    pass

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

                report_ts = _report_ts_seconds(report_data)
                with state_lock:
                    last_seen_map[node_ip] = report_ts
                    current_state = node_lifecycle.get(node_ip, 0)

                try:
                    if current_state == 0:
                        neighbor_manager.initial_blind_assignment(node_ip)
                        with state_lock:
                            node_lifecycle[node_ip] = 1
                    elif current_state == 1:
                        reported_links = report_data.get("links", {}) or {}
                        expected_min = min(
                            neighbor_manager.k,
                            len(neighbor_manager.get_candidate_pool(node_ip)),
                        )
                        if len(reported_links) >= expected_min:
                            neighbor_manager.optimized_final_assignment(node_ip, reported_links)
                            with state_lock:
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
                                with state_lock:
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
                with state_lock:
                    node_lifecycle.pop(node_ip, None)
                    last_seen_map.pop(node_ip, None)

    threading.Thread(target=_timeout_pruner, daemon=True).start()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t

