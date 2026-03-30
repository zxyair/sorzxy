import json
import os
import urllib.request
import socket
import time
from typing import Any, Dict, Optional, Tuple

import etcd3

from common.config import get_settings

def _load_sar_config() -> Dict[str, Any]:
    """
    从集中配置读取 SAR 业务端口。

    字段语义使用现有代码的 SERVICE_PORT（JSON key 也叫 SERVICE_PORT）。
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(base_dir, "config", "sar_config.json")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _fetch_public_ip_ifconfig_me() -> Optional[str]:
    """
    优先使用 ifconfig.me 返回公网 IP。
    """
    try:
        with urllib.request.urlopen("https://ifconfig.me/ip", timeout=3) as resp:
            ip = resp.read().decode("utf-8").strip()
            return ip if ip else None
    except Exception as e:
        return None


def _detect_host_ip_udp_fallback() -> Optional[str]:
    """
    最后兜底：用 UDP connect 得到本机出接口源地址（可能是私网，但用于避免空值崩溃）。
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


def _detect_public_sar_ip() -> Tuple[str, str]:
    """
    统一公网 IP 探测策略：
    1) ifconfig.me
    2) SAR_IP 环境变量
    3) UDP 源地址兜底
    """
    ip = _fetch_public_ip_ifconfig_me()
    if ip:
        return ip, "ifconfig.me"

    env_ip = os.getenv("SAR_IP")
    if env_ip:
        return env_ip, "env"

    udp_ip = _detect_host_ip_udp_fallback()
    if udp_ip:
        return udp_ip, "udp_fallback"
    return "127.0.0.1", "default"


settings = get_settings()
_sar_config = _load_sar_config()
SERVICE_PORT = int(_sar_config.get("SERVICE_PORT", os.getenv("SERVICE_PORT", "8088")))
SAR_IP, _sar_ip_source = _detect_public_sar_ip()

print(f"[*] SAR public ip resolved: {SAR_IP} (source={_sar_ip_source})")

def register_sar_lifecycle():
    print("=====================================================")
    print(f"🎯 SAR 业务容灾节点启动注册引擎 | IP: {SAR_IP}")
    print("=====================================================")
    
    try:
        etcd_client = etcd3.client(host=settings.etcd_host, port=settings.etcd_port)
    except Exception as e:
        print(f"[-] 无法连接到 etcd: {e}")
        return

    # SAR 专属的注册目录
    registry_key = f"/network/sar/{SAR_IP}"
    
    # 业务元数据
    sar_meta = {
        "service_type": "voip_dr", # 语音容灾业务
        "port": SERVICE_PORT,
        "status": "UP",
        "register_time": time.time()
    }

    try:
        while True:
            # 持续发送心跳，宣告自己活着
            etcd_client.put(registry_key, json.dumps(sar_meta))
            print(f"[*] 💓 SAR [{SAR_IP}] 存活心跳已广播至全网 -> etcd ({registry_key})")
            time.sleep(5) # 5秒一次心跳
            
    except KeyboardInterrupt:
        print(f"\n[!] SAR [{SAR_IP}] 节点下线，清理注册信息...")
        etcd_client.delete(registry_key)

if __name__ == "__main__":
    register_sar_lifecycle()