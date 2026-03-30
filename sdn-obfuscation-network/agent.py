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
            "location": "agent.py:agentlog",
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
    "H_env_agent",
    "python runtime",
    {"executable": _agent_sys.executable, "version": _agent_sys.version.splitlines()[0], "argv0": (_agent_sys.argv[0] if _agent_sys.argv else None)},
)
#endregion agent log

import etcd3
import json
import os
import socket
import urllib.request
import subprocess
import threading
import time
from typing import Optional, Tuple

import psutil

from common.config import get_settings
from common.etcd_keys import neighbor_config_key, rules_prefix, status_key
from common.logging import setup_logging
from sor.tunnel_manager import TunnelManager, TunnelSpec
from sor.qos_stats import QosWindowStats

os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GRPC_TRACE"] = "none"

settings = get_settings()
log = setup_logging("sor.agent", level=settings.log_level)
# --- 全局动态变量 ---
# 初始为空，等待 DS 下发指令
DYNAMIC_NEIGHBORS = [] 
NEIGHBOR_LOCK = threading.Lock() # 线程锁，防止读写冲突
# 动态获取本机 IP
# ==========================================
# 1. 动态邻居配置监听线程 (听令模块)
# ==========================================
def watch_neighbor_config():
    """
    持续监听 etcd 中的配置路径：/network/config/{MY_IP}/neighbors
    DS 会往这里写入如 ["172.16.0.12", "172.16.0.14"] 的列表
    """
    config_path = neighbor_config_key(MY_IP)
    print(f"[*] 配置监听启动：正在等待 DS 分配探测任务于 {config_path}")
    
    # 阻塞式监听
    events_iterator, _ = ETCD_CLIENT.watch_prefix(config_path)
    for event in events_iterator:
        if type(event).__name__ == 'PutEvent':
            try:
                new_list = json.loads(event.value.decode('utf-8'))
                with NEIGHBOR_LOCK:
                    global DYNAMIC_NEIGHBORS
                    DYNAMIC_NEIGHBORS = new_list
                # print(f"\n[任务更新] 📢 DS 下发了新的探测邻居: {DYNAMIC_NEIGHBORS}")
            except Exception as e:
                print(f"[-] 解析邻居配置失败: {e}")

# ==========================================
# 2. 修改后的状态感知引擎 (执行模块)
# =========================================
def _fetch_public_ip_ifconfig_me() -> Optional[str]:
    """
    优先使用 ifconfig.me 获取公网 IP。
    """
    try:
        with urllib.request.urlopen("https://ifconfig.me/ip", timeout=3) as resp:
            ip = resp.read().decode("utf-8").strip()
            return ip if ip else None
    except Exception as e:
        return None


def _detect_host_ip_udp_fallback() -> Optional[str]:
    """
    最后兜底：用 UDP connect 得到本机出接口源地址（可能是私网）。
    """
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return None
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def _detect_public_node_ip() -> Tuple[str, str]:
    """
    统一公网 IP 探测策略：
    1) ifconfig.me
    2) SOR_IP 环境变量
    3) UDP 源地址兜底
    """
    ip = _fetch_public_ip_ifconfig_me()
    if ip:
        return ip, "ifconfig.me"

    env_ip = os.getenv("SOR_IP")
    if env_ip:
        return env_ip, "env"

    udp_ip = _detect_host_ip_udp_fallback()
    if udp_ip:
        return udp_ip, "udp_fallback"
    return "127.0.0.1", "default"

MY_IP, _my_ip_source = _detect_public_node_ip()
print(f"[*] SOR public ip resolved: {MY_IP} (source={_my_ip_source})")
ETCD_CLIENT = etcd3.client(host=settings.etcd_host, port=settings.etcd_port)

_qos_stats = QosWindowStats(window_size=settings.qos_window_size, min_samples=settings.qos_min_samples)

# ==========================================
# 模块 1：全局状态感知与上报引擎 (后台线程)
# ==========================================
# todo 探测程序需要更新和修改
def run_telemetry_sensor():
    print(f"[*] 状态感知模块已启动，开始周期性监测自身与链路...")
    
    while True:
        try:
            # 1. 节点自身硬件资源监控
            cpu_usage = psutil.cpu_percent(interval=None)
            mem_info = psutil.virtual_memory()
            mem_usage = mem_info.percent
            
            # 计算综合负载率 (如果逼近物理极限，如 >90%，可触发告警)
            is_overloaded = (cpu_usage > 90.0 or mem_usage > 90.0)
            
            # 2. 邻居链路健康度高频探测 (RTT 毫秒)
            link_status = {}
            with NEIGHBOR_LOCK:
                current_targets = list(DYNAMIC_NEIGHBORS)

            for neighbor in current_targets:
                # 执行 ping 探测逻辑
                ping_cmd = ["ping", "-c", "1", "-W", str(settings.ping_timeout_s), neighbor]
                try:
                    output = subprocess.check_output(ping_cmd, stderr=subprocess.STDOUT, universal_newlines=True)
                    if "time=" in output:
                        rtt_str = output.split("time=")[1].split(" ")[0]
                        rtt_ms = float(rtt_str)
                        _qos_stats.add_success(neighbor, rtt_ms)
                        snap = _qos_stats.get_snapshot(neighbor)

                        # bw: V1 approximation (configurable), penalize if node overloaded
                        bw = float(settings.default_bw_mbps)
                        if cpu_usage >= settings.bw_overload_cpu_threshold or is_overloaded:
                            bw = bw * float(settings.bw_overload_penalty_ratio)

                        link_status[neighbor] = {
                            "rtt_ms": rtt_ms,
                            "status": "UP",
                            "bw": bw,
                            "loss": 0.0 if snap is None else snap.loss,
                            "jitter": 0.0 if snap is None else snap.jitter_ms,
                        }
                except:
                    _qos_stats.add_loss(neighbor)
                    snap = _qos_stats.get_snapshot(neighbor)

                    bw = float(settings.default_bw_mbps)
                    if cpu_usage >= settings.bw_overload_cpu_threshold or is_overloaded:
                        bw = bw * float(settings.bw_overload_penalty_ratio)

                    link_status[neighbor] = {
                        "rtt_ms": 9999,
                        "status": "DOWN",
                        "bw": bw,
                        "loss": 0.0 if snap is None else snap.loss,
                        "jitter": 0.0 if snap is None else snap.jitter_ms,
                    }
            
            # 3. 序列化并上报至控制面 (etcd)
            report_data = {
                "timestamp": time.time(),
                "node_status": {
                    "cpu_percent": cpu_usage,
                    "mem_percent": mem_usage,
                    "is_overloaded": is_overloaded
                },
                "links": link_status
            }
            
            # 写入 etcd (作为轻量级内部控制信道)
            # Attach a short lease so stale status keys auto-expire after agent exit.
            status_payload = json.dumps(report_data)
            if int(settings.status_ttl_s) > 0:
                lease = ETCD_CLIENT.lease(int(settings.status_ttl_s))
                ETCD_CLIENT.put(status_key(MY_IP), status_payload, lease=lease)
            else:
                ETCD_CLIENT.put(status_key(MY_IP), status_payload)
            
            if is_overloaded:
                print(f"[!] ⚠️ 主动降级告警：节点 {MY_IP} 负载过高 (CPU: {cpu_usage}%, Mem: {mem_usage}%)")
            
            # 探测周期 (默认 3 秒一次)
            time.sleep(settings.telemetry_interval_s)
            
        except Exception as e:
            print(f"[-] 感知模块异常: {e}")
            time.sleep(5)

# ==========================================
# 模块 2：原有的流表监听模块 (监听 DS 下发的规则)
# ==========================================
_tunnels = TunnelManager(settings.tinymapper_path)

def listen_for_rules():
    """
    SOR 节点数据面核心：事件驱动的流表下发与隧道编排引擎
    """
    MY_RULE_DIR = rules_prefix(MY_IP)
    print(f"[*] 数据转发平面已就绪，正在 Watch 监听控制信道: {MY_RULE_DIR}")
    
    # 阻塞式监听属于自己的流表目录，零轮询开销
    events_iterator, cancel = ETCD_CLIENT.watch_prefix(MY_RULE_DIR)

    for event in events_iterator:
        try:
            event_type = type(event).__name__
            key = event.key.decode('utf-8')
            
            if event_type == 'PutEvent':
                # ==========================================
                # 动作 1：新建隐匿隧道 (大脑下发流表)
                # ==========================================
                val = json.loads(event.value.decode('utf-8'))
                tid = val.get("tunnel_id")
                lp = val.get("lp")
                rip = val.get("rip")
                rp = val.get("rp")
                
                print(f"\n[+] 收到 DS 新规则！准备建立隐匿隧道 [{tid}]: 本地端口 {lp} -> 下一跳 {rip}:{rp}")
                
                # 动态拉起底层的 tinymapper 进程 (-u 开启 UDP 极速转发)
                # 注意：确保同目录下有编译好的 tinymapper 可执行文件
                proc = _tunnels.apply(
                    TunnelSpec(
                        tunnel_id=int(tid),
                        listen_port=int(lp),
                        remote_ip=str(rip),
                        remote_port=int(rp),
                        udp=True,
                    )
                )
                print(f"[+] 隧道 [{tid}] 极速盲接成功！底层进程 PID: {proc.pid}")

            elif event_type == 'DeleteEvent':
                # ==========================================
                # 动作 2：拆除隐匿隧道 (大脑重路由或业务结束)
                # ==========================================
                # 假设 key 的格式为 /network/rules/172.16.0.11/tunnel_1024
                tid_str = key.split('_')[-1] 
                if tid_str.isdigit():
                    tid = int(tid_str)
                    if _tunnels.is_active(tid):
                        print(f"\n[-] 收到 DS 销毁指令！正在强制拆除隧道 [{tid}]...")
                        _tunnels.stop(tid)
                        print(f"[-] 隧道 [{tid}] 已安全释放，端口已回收。")
                        
        except Exception as e:
            print(f"[!] 处理流表事件时发生异常: {e}")
            # 继续监听，防止单个异常包导致 Agent 崩溃退出
            continue
if __name__ == '__main__':
    import signal
    import sys

    def _shutdown_handler(signum, frame):
        try:
            print("\n[Agent] 捕获到终止信号，正在清理所有活跃隧道...")
            _tunnels.stop_all()
        finally:
            sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    # 线程 A：监听 DS 下发的邻居配置 (New!)
    config_thread = threading.Thread(target=watch_neighbor_config, daemon=True)
    config_thread.start()
    # 线程 B:启动后台感知线程
    telemetry_thread = threading.Thread(target=run_telemetry_sensor, daemon=True)
    telemetry_thread.start()
    
    # 启动主线程的流表监听
    listen_for_rules()
    
    # 防止主线程退出
    while True:
        time.sleep(1)