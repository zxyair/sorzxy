# -*- coding: utf-8 -*-
#region agent log
import json as _agent_json
import os as _agent_os
import sys as _agent_sys
import time as _agent_time

def _agent_log(hypothesisId, message, data=None, runId="pre-fix"):
    try:
        payload = {
            "sessionId": "d1d091",
            "runId": runId,
            "hypothesisId": hypothesisId,
            "location": "ds_server.py:agentlog",
            "message": message,
            "data": data or {},
            "timestamp": int(_agent_time.time() * 1000),
        }
        _agent_os.makedirs("/home/ubuntu/sorzxy/.cursor", exist_ok=True)
        with open("/home/ubuntu/sorzxy/.cursor/debug-d1d091.log", "a", encoding="utf-8") as f:
            f.write(_agent_json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass

_agent_log(
    "H_env",
    "python runtime",
    {"executable": _agent_sys.executable, "version": _agent_sys.version.splitlines()[0], "argv0": (_agent_sys.argv[0] if _agent_sys.argv else None)},
)
#endregion agent log

import grpc
import os
try:
    import etcd3
    #region agent log
    _agent_log("H_deps", "import etcd3 ok", {"etcd3": getattr(etcd3, "__version__", None)})
    #endregion agent log
except Exception as e:
    #region agent log
    _agent_log("H_deps", "import etcd3 failed", {"errorType": type(e).__name__, "error": str(e)})
    #endregion agent log
    raise
import json
import time
import random
from concurrent import futures
import networkx as nx
import threading
import control_pb2
import control_pb2_grpc

from common.config import get_settings
from common.etcd_keys import neighbor_config_key, rule_key, sar_key, sar_prefix, status_prefix
from common.logging import setup_logging
from ds.topology_engine import TopologyEngine
from ds.watchers import start_telemetry_watcher
from ds.routing.compiler import compile_portmapped_tunnel
from ds.routing.constraints import ConstraintParams, filter_path_constraints

settings = get_settings()
log = setup_logging("ds.server", level=settings.log_level)

#邻居指派算法管理器
import json
import random
import threading

class NeighborManager:
    def __init__(self, etcd_client):
        self.etcd = etcd_client
        # 从集中配置 / 环境变量中读取邻居相关参数
        self.k = int(settings.neighbor_k)               # 最终每个节点维系的法定邻居数
        self.discovery_count = int(settings.discovery_count)  # 第一阶段盲探任务的节点数
        # 核心数据结构：记录每个节点被指派为探测目标的次数
        self.in_degree_map = {} 
        self.lock = threading.Lock()

    def get_candidate_pool(self, exclude_ip):
        """获取当前所有在线的可选 SOR 节点"""
        # 从 etcd 的 status 目录获取当前活跃节点
        all_keys = self.etcd.get_prefix(status_prefix())
        nodes = []
        for _, meta in all_keys:
            ip = meta.key.decode('utf-8').split('/')[-1]
            if ip != exclude_ip and ":" not in ip: # 排除自身和带端口的临时实例
                nodes.append(ip)
        return nodes

    def initial_blind_assignment(self, new_node_ip):
        """阶段 1：盲探指派。新节点上线，随机分配种子节点测距。"""
        candidates = self.get_candidate_pool(new_node_ip)
        if not candidates:
            print(f"[邻居指配] ⏳ 暂无其他在线节点，{new_node_ip} 等待中...")
            return
        
        # 随机挑选 discovery_count 个节点进行 RTT 盲探
        probe_targets = random.sample(candidates, min(len(candidates), self.discovery_count))
        
        config_key = neighbor_config_key(new_node_ip)
        self.etcd.put(config_key, json.dumps(probe_targets))
        print(f"[邻居指配-阶段1] 🚩 为新节点 {new_node_ip} 下发盲探任务: {probe_targets}")

    def optimized_final_assignment(self, node_ip, reported_links):
        """阶段 2：入度均衡 + 就近固化。根据 RTT 回传执行优选。"""
        # 过滤掉无效链路
        valid_links = {ip: data['rtt_ms'] for ip, data in reported_links.items() if data['rtt_ms'] < 2000}
        if not valid_links:
            return

        with self.lock:
            weighted_scores = []
            for target_ip, rtt in valid_links.items():
                # 入度均衡公式：Score = RTT*0.7 + (入度*惩罚系数)*0.3
                # 惩罚系数 40 表示每多一个入度，等效于延迟增加 ~17ms
                current_in_degree = self.in_degree_map.get(target_ip, 0)
                score = (rtt * 0.7) + (current_in_degree * 40 * 0.3)
                weighted_scores.append((target_ip, score))

            # 按综合得分排序并选取前 K 个
            sorted_by_score = sorted(weighted_scores, key=lambda x: x[1])
            best_k = [ip for ip, score in sorted_by_score[:self.k]]

            # 更新入度表（简单增加逻辑，实际论文中可描述更复杂的入度动态回收）
            for ip in best_k:
                self.in_degree_map[ip] = self.in_degree_map.get(ip, 0) + 1
        
        config_key = neighbor_config_key(node_ip)
        self.etcd.put(config_key, json.dumps(best_k))
        
        print(f"[邻居指配-阶段2] ✅ 节点 {node_ip} 完成就近+负载均衡指派: {best_k}")
        print(f"  └─ 实时全网入度(被探测压力): {self.in_degree_map}")

topo_engine = TopologyEngine.create()
print("[*] 🧠 拓扑大脑初始化完毕 (当前为纯白板状态，等待节点主动注册...)")


def calculate_cmo_pdfs(graph: nx.DiGraph, source: str, target: str, req_bw: float):
    # ==========================================
    # V1+: multi-QoS constraints + random choice in feasible pool
    # ==========================================
    params = ConstraintParams(
        min_hops=int(settings.min_hops),
        max_hops=int(settings.max_hops),
        max_delay_ms=float(settings.max_voice_delay_ms),
        max_jitter_ms=float(settings.max_jitter_ms),
        max_loss_rate=float(settings.max_loss_rate),
        min_bw_mbps=max(float(settings.min_bw_mbps), float(req_bw)),
    )

    valid_obfuscated_paths = []
    reject_counts = {"delay": 0, "jitter": 0, "loss": 0, "bw": 0, "min_hops": 0, "max_hops": 0}

    for path in nx.all_simple_paths(graph, source, target, cutoff=params.max_hops):
        ok, reasons, metrics = filter_path_constraints(graph, path, params)
        if not ok:
            for k in reasons.keys():
                if k in reject_counts:
                    reject_counts[k] += 1
            continue

        assert metrics is not None
        valid_obfuscated_paths.append(
            {"path": path, "delay": metrics.delay_ms, "hop_count": len(path) - 2}
        )

    diag = {
        "constraints": {
            "min_hops": params.min_hops,
            "max_hops": params.max_hops,
            "max_delay_ms": params.max_delay_ms,
            "max_jitter_ms": params.max_jitter_ms,
            "max_loss_rate": params.max_loss_rate,
            "min_bw_mbps": params.min_bw_mbps,
        },
        "rejects": reject_counts,
        "feasible_paths": len(valid_obfuscated_paths),
        "source": source,
        "target": target,
    }

    if not valid_obfuscated_paths:
        print(
            "[算法引擎] ❌ 算路驳回：无合规路径 | "
            f"rejects(delay={reject_counts['delay']}, jitter={reject_counts['jitter']}, loss={reject_counts['loss']}, "
            f"bw={reject_counts['bw']}, min_hops={reject_counts['min_hops']}, max_hops={reject_counts['max_hops']})"
        )
        return None, diag

    chosen_route = random.choice(valid_obfuscated_paths)
    print(
        f"\n[算法引擎] 🔍 语音 QoS 空间探索完毕，共锁定 {len(valid_obfuscated_paths)} 条达标隐匿路径。"
    )
    print(
        f"[算法引擎] 🎯 PDFS 决断成功！最终级联: {chosen_route['hop_count']} 跳跳板, 预期时延: {chosen_route['delay']:.2f}ms"
    )
    print(
        "[算法引擎] constraints | "
        f"max_delay_ms={params.max_delay_ms}, max_jitter_ms={params.max_jitter_ms}, "
        f"max_loss_rate={params.max_loss_rate}, min_bw_mbps={params.min_bw_mbps}, "
        f"min_hops={params.min_hops}, max_hops={params.max_hops}"
    )
    diag["chosen"] = {"delay_ms": chosen_route["delay"], "hop_count": chosen_route["hop_count"]}
    return chosen_route["path"], diag
# ==========================================
# 2. 目录服务器网络控制平面
# ==========================================
etcd_client = etcd3.client(host=settings.etcd_host, port=settings.etcd_port)

class DirectoryServerServicer(control_pb2_grpc.DirectoryServerServicer):
    def RequestTunnel(self, request, context):
        # ==========================================
        # 1. 提取真实的 SMR 终端物理 IP 与端口
        # ==========================================
        peer_info = context.peer()
        if peer_info.startswith("ipv4:"):
            smr_endpoint = peer_info[5:]
        elif peer_info.startswith("ipv6:"):
            smr_endpoint = peer_info[5:]
        else:
            smr_endpoint = peer_info

        print(f"\n[DS 控制平面] 📡 接收到 SMR 终端 [{smr_endpoint}] 接入请求 -> 目标: {request.target_sar_ip}")
        
        # ==========================================
        # 2. 随机盲选入口与临时图挂载 (MVP 方案)
        # ==========================================
        # 从图中获取当前所有存活的 SOR 节点 (排除 SMR 自身和目标 SAR)
        alive_nodes = [n for n in topo_engine.graph.nodes if ":" not in n and n not in ["SMR", request.target_sar_ip]]
        
        if not alive_nodes:
            print("[DS 接入网关] ❌ 全网无存活节点，接入失败！")
            return control_pb2.TunnelResp(success=False, message="全网无存活节点，无法接入！")
            
        # 多入口候选 + 逐个尝试：避免随机入口导致“无可行路径”直接失败
        max_attempts = min(len(alive_nodes), int(os.getenv("INGRESS_TRIES", "5")))
        ingress_candidates = random.sample(alive_nodes, k=max_attempts)
        print(f"[DS 接入网关] 🎲 入口候选({len(ingress_candidates)}/{len(alive_nodes)}): {ingress_candidates}")

        last_diag = None
        path = None
        chosen_ingress = None
        for ingress_node in ingress_candidates:
            # 【关键步骤】在工作图中临时挂载 SMR：避免污染全局拓扑与并发读写问题
            work_graph = topo_engine.graph.copy()
            work_graph.add_edge(smr_endpoint, ingress_node, bw=1000.0, delay=10.0)
            # 触发 CMO-PDFS 算路
            candidate_path, diag = calculate_cmo_pdfs(work_graph, smr_endpoint, request.target_sar_ip, request.req_bandwidth)
            last_diag = diag
            if candidate_path:
                path = candidate_path
                chosen_ingress = ingress_node
                break

        # # ==========================================
        # # 🌟 修复孤岛：为模拟迷宫接上通往 8.8.8.8 的出口边界网关
        # # ==========================================
        # topo_engine.graph.add_edge("10.0.0.108", request.target_sar_ip, bw=1000.0, delay=8.0)
        # topo_engine.graph.add_edge("10.0.0.109", request.target_sar_ip, bw=1000.0, delay=11.0)
        # topo_engine.graph.add_edge("10.0.0.107", request.target_sar_ip, bw=1000.0, delay=11.0)
        # topo_engine.graph.add_edge("10.0.0.106", request.target_sar_ip, bw=1000.0, delay=11.0)
        # topo_engine.graph.add_edge("10.0.0.105", request.target_sar_ip, bw=1000.0, delay=11.0)
        # topo_engine.graph.add_edge("10.0.0.104", request.target_sar_ip, bw=1000.0, delay=11.0)
        # topo_engine.graph.add_edge("10.0.0.103", request.target_sar_ip, bw=1000.0, delay=11.0)
        
        if not path:
            print("[DS 控制平面] ⚠️ 算路失败：当前全网资源无法满足 QoS 约束！")
            payload = {
                "code": "NO_FEASIBLE_PATH",
                "message": "无合规路径（可能由loss/jitter/delay/bw/hops任一约束触发）",
                "tries": ingress_candidates,
                "diag": last_diag,
            }
            return control_pb2.TunnelResp(success=False, message=json.dumps(payload, ensure_ascii=False))

        # ==========================================
        # 4. 全链路动态端口编排与日志格式化
        # ==========================================
        assigned_tunnel_id = int(time.time() * 1000) % 100000
        # 从 etcd 获取 SAR 注册的端口
        sar_value, _ = etcd_client.get(sar_key(request.target_sar_ip))
        if sar_value:
            sar_meta = json.loads(sar_value.decode('utf-8'))
            target_final_port = sar_meta.get('port', 53)
            print(f"[DS 控制平面] ✅ 使用SAR注册端口: {target_final_port}")
        else:
            target_final_port = 53
            print(f"[DS 控制平面] ⚠️ SAR未连接到隐匿网络，使用默认端口 53")

        compiled = compile_portmapped_tunnel(
            path=path,
            tunnel_id=assigned_tunnel_id,
            target_final_port=target_final_port,
            port_min=settings.port_min,
            port_max=settings.port_max,
            ttl_seconds=(settings.rule_ttl_s if settings.rule_ttl_s > 0 else None),
        )

        # 拼接用于打印的带端口全路径字符串
        detailed_path = [smr_endpoint]  # 起点自带 IP:Port
        for node in path[1:-1]:
            detailed_path.append(f"{node}:{compiled.node_ports[node]}")
        detailed_path.append(f"{path[-1]}:{target_final_port}")  # 终点 SAR
        
        if chosen_ingress:
            print(f"[DS 接入网关] ✅ 入口选择成功: {chosen_ingress}")
        print(f"[DS 控制平面] 🎯 隐匿隧道编排成功！全链路跃点: {' -> '.join(detailed_path)}")
        
        # ==========================================
        # 5. 向路径上的每一个中间节点下发级联流表
        # ==========================================
        lease = None
        if settings.rule_ttl_s > 0:
            lease = etcd_client.lease(settings.rule_ttl_s)

        for current_node, rule_data in compiled.rules:
            etcd_client.put(
                rule_key(current_node, assigned_tunnel_id),
                json.dumps(rule_data),
                lease=lease,
            )
            print(
                f"  ├─ ⚡ 流表下发至 {current_node} -> 监听:{rule_data['lp']} 转发至:{rule_data['rip']}:{rule_data['rp']}"
            )

        # ==========================================
        # 6. 组装响应，返回首跳信息给客户端
        # ==========================================
        first_hop_ip = compiled.first_hop_ip
        first_hop_port = compiled.first_hop_port
        
        return control_pb2.TunnelResp(
            success=True,
            tunnel_id=assigned_tunnel_id,
            first_hop_ip=first_hop_ip,
            first_hop_port=first_hop_port,
            message=f"隐匿级联成功！追踪点为 {smr_endpoint}"
        )
def serve():
    # --- 在启动 gRPC 服务前，先拉起遥测监听线程 ---
    start_telemetry_watcher(
        etcd_client=etcd_client,
        topo_engine=topo_engine,
        neighbor_manager=neighbor_manager,
        node_lifecycle=node_lifecycle,
    )
    # 启动 SAR 目标感知引擎
    threading.Thread(target=sar_discovery_watcher, daemon=True).start()

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    control_pb2_grpc.add_DirectoryServerServicer_to_server(DirectoryServerServicer(), server)
    server.add_insecure_port(settings.ds_grpc_bind)
    server.start()
    print("[*] 智融标识目录服务器 (DS) 已启动")
    print("[*] CMO-PDFS 隐匿算路引擎已上线，等待 SMR 终端接入...")
    server.wait_for_termination()

neighbor_manager = NeighborManager(etcd_client)
# 记录节点状态：0-新发现, 1-盲探中, 2-已固化
node_lifecycle = {}

#监控sar
def sar_discovery_watcher():
    """
    DS 大脑专属线程：监听业务目标 (SAR) 上线，并向全网 SOR 下发探测指令
    """
    print("[DS 控制平面] 👁️ SAR 目标发现引擎已启动，正在全局监听...")
    known_sars = set()
    
    while True:
        try:
            # 1. 从 etcd 获取当前存活的所有 SAR 目标
            current_sars = set()
            for value, meta in etcd_client.get_prefix(sar_prefix()):
                sar_ip = meta.key.decode('utf-8').split('/')[-1]
                current_sars.add(sar_ip)

            # 2. 如果目标发生变更（新上线或掉线）
            if current_sars != known_sars:
                print(f"\n[DS 拓扑管理] 🔄 感知到 SAR 节点变更: {list(current_sars)}")
                known_sars = current_sars

            # 3. 周期性同步：确保“后上线的 SOR”也能收到最新 SAR 探测目标
            if known_sars:
                for _, meta in etcd_client.get_prefix(status_prefix()):
                    sor_ip = meta.key.decode('utf-8').split('/')[-1]

                    config_val, _ = etcd_client.get(neighbor_config_key(sor_ip))
                    neighbors = json.loads(config_val.decode('utf-8')) if config_val else []

                    # 保留原有 SOR 网格邻居（默认约定为 10.*），追加所有 SAR 目标
                    base_neighbors = [ip for ip in neighbors if ip.startswith("10.")]
                    new_neighbors = sorted(set(base_neighbors).union(known_sars))
                    etcd_client.put(neighbor_config_key(sor_ip), json.dumps(new_neighbors))
                    
        except Exception as e:
            pass
            
        time.sleep(3) # 大脑每 3 秒巡检一次全局目标

if __name__ == '__main__':
    serve()