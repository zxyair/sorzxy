from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


_CONFIG_CACHE: Dict[str, Any] = {}


def _load_config_file() -> Dict[str, Any]:
    """
    尝试从 config/ds_config.json 加载集中配置。

    设计约定：
    - 如果文件不存在或解析失败，则返回空 dict。
    - 配置文件中的值优先级高于环境变量和默认值。
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE:
        return _CONFIG_CACHE

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(base_dir, "config", "ds_config.json")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                _CONFIG_CACHE = data
            else:
                _CONFIG_CACHE = {}
    except FileNotFoundError:
        _CONFIG_CACHE = {}
    except Exception:
        # 配置文件格式错误时，回退到环境变量 + 默认值
        _CONFIG_CACHE = {}

    return _CONFIG_CACHE


def _cfg(path: str, default: Any) -> Any:
    """
    从集中配置中按“a.b.c”路径取值；若不存在则返回 default。
    """
    data = _load_config_file()
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _from_config_or_env(path: str, env_name: str, default: Any) -> Any:
    """
    先从集中配置文件中读取，如果该路径不存在，再读取环境变量，最后回退到默认值。
    """
    sentinel = object()
    val = _cfg(path, sentinel)
    if val is not sentinel:
        return val
    raw = os.getenv(env_name)
    if raw is None or raw == "":
        return default
    # 简单根据 default 类型做一次转换
    if isinstance(default, bool):
        return raw.lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        try:
            return int(raw)
        except ValueError:
            return default
    if isinstance(default, float):
        try:
            return float(raw)
        except ValueError:
            return default
    return raw


@dataclass(frozen=True)
class Settings:
    # --- 基础连接 ---
    etcd_host: str = _from_config_or_env("etcd.host", "ETCD_HOST", "127.0.0.1")
    etcd_port: int = _from_config_or_env("etcd.port", "ETCD_PORT", 2379)

    # DS gRPC 监听地址（供 DS 进程绑定使用）
    ds_grpc_bind: str = _from_config_or_env("ds.grpc_bind", "DS_GRPC_BIND", "[::]:50051")
    # DS gRPC 访问地址（供 SMR / 其他客户端访问），默认指向本地 50051
    ds_grpc_target: str = _from_config_or_env("ds.grpc_target", "DS_GRPC_TARGET", "127.0.0.1:50051")

    telemetry_interval_s: float = _get_float("TELEMETRY_INTERVAL_S", 3.0)
    ping_timeout_s: int = _get_int("PING_TIMEOUT_S", 1)

    # --- QoS 约束（集中配置 + 环境变量） ---
    max_voice_delay_ms: float = _from_config_or_env("qos.max_voice_delay_ms", "MAX_VOICE_DELAY_MS", 150.0)

    # QoS derivation (ping-based V1)
    qos_window_size: int = _get_int("QOS_WINDOW_SIZE", 40)  # ~2min @ 3s interval
    qos_min_samples: int = _get_int("QOS_MIN_SAMPLES", 5)
    default_bw_mbps: float = _get_float("DEFAULT_BW_MBPS", 200.0)
    bw_overload_cpu_threshold: float = _get_float("BW_OVERLOAD_CPU_THRESHOLD", 90.0)
    bw_overload_penalty_ratio: float = _get_float("BW_OVERLOAD_PENALTY_RATIO", 0.5)  # multiply bw by this when overloaded

    # Multi-QoS routing constraints (DS)
    max_jitter_ms: float = _from_config_or_env("qos.max_jitter_ms", "MAX_JITTER_MS", 30.0)
    max_loss_rate: float = _from_config_or_env("qos.max_loss_rate", "MAX_LOSS_RATE", 0.01)
    min_bw_mbps: float = _from_config_or_env("qos.min_bw_mbps", "MIN_BW_MBPS", 1.0)
    min_hops: int = _from_config_or_env("qos.min_hops", "MIN_HOPS", 2)  # edges; corresponds to >=3 nodes
    max_hops: int = _from_config_or_env("qos.max_hops", "MAX_HOPS", 5)

    # tinyPortMapper/tinymapper path. Default matches current behavior (cwd-based).
    tinymapper_path: str = os.getenv("TINYPORTMAPPER_PATH", "./tinymapper")

    # Dynamic port range for DS compiler.
    port_min: int = _from_config_or_env("tunnel.port_min", "PORT_MIN", 10000)
    port_max: int = _from_config_or_env("tunnel.port_max", "PORT_MAX", 60000)

    # Tunnel rule TTL for etcd lease (seconds). 0 disables lease-based expiry.
    rule_ttl_s: int = _from_config_or_env("tunnel.rule_ttl_s", "RULE_TTL_S", 60)

    # 邻居拓扑相关参数（DS 使用）
    neighbor_k: int = _from_config_or_env("neighbor.k", "NEIGHBOR_K", 10)
    discovery_count: int = _from_config_or_env("neighbor.discovery_count", "NEIGHBOR_DISCOVERY_COUNT", 10)

    log_level: str = _from_config_or_env("log_level", "LOG_LEVEL", "INFO")


def get_settings() -> Settings:
    return Settings()

