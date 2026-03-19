#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
End-to-end simulation script:
- clears etcd /network/ prefix
- starts DS (gRPC) and SAR register subprocesses
- spawns a simulated SOR cluster that interacts with DS neighbor assignment
- issues multiple SMR RequestTunnel calls
- reconstructs chosen paths from etcd rules and prints routing summary

Usage (within venv):
  python scripts/simulate_e2e_routing.py
"""

from __future__ import annotations

import json
import math
import os
import random
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

if sys.version_info[0] < 3:
    raise SystemExit("This script requires Python 3. Run: python3 scripts/simulate_e2e_routing.py")

import logging
import etcd3
import grpc

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import control_pb2
import control_pb2_grpc
from common.etcd_keys import neighbor_config_key, rule_key, sar_key, status_key


#region agent log
def _agent_log(hypothesisId: str, message: str, data: Optional[dict] = None, *, runId: str = "pre-fix") -> None:
    """
    Debug-mode runtime evidence logger (NDJSON).
    Keep payload small and avoid secrets/PII.
    """
    try:
        payload = {
            "sessionId": "2251c0",
            "runId": runId,
            "hypothesisId": hypothesisId,
            "location": "scripts/simulate_e2e_routing.py:agentlog",
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        os.makedirs("/home/ubuntu/sorzxy/.cursor", exist_ok=True)
        with open("/home/ubuntu/sorzxy/.cursor/debug-2251c0.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
#endregion agent log


def _ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def _setup_file_logger(name: str, log_path: str) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    # Avoid duplicate handlers if main() is called repeatedly.
    for h in list(logger.handlers):
        logger.removeHandler(h)
    # Append by default (required by smoke use-cases)
    fh = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(fh)
    return logger


def _tee_stream_to_file(
    stream,
    *,
    log_fh,
    prefix: str,
    stop_event: threading.Event,
    echo_to_stdout: bool = True,
) -> threading.Thread:
    """
    Read a subprocess text stream line-by-line, write to file, and echo to stdout.
    """

    def _run() -> None:
        try:
            for line in iter(stream.readline, ""):
                if stop_event.is_set():
                    break
                # Normalize odd line terminators (e.g., NEL) for editor friendliness.
                if "\x85" in line:
                    line = line.replace("\x85", "\n")
                if line and not line.endswith("\n"):
                    line += "\n"
                # Prefix each line with local timestamp for consistency across sources.
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                out = f"{ts} {line}"
                log_fh.write(out)
                log_fh.flush()
                if echo_to_stdout:
                    sys.stdout.write(f"[{prefix}] {out}")
                    sys.stdout.flush()
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def _log_line(logger: logging.Logger, msg: str) -> None:
    """
    Replace console prints with file logging to keep runs quiet.
    """
    try:
        logger.info("%s", msg)
    except Exception:
        pass


def _format_reject_message(raw: str) -> str:
    """
    DS may return a JSON payload for structured diagnostics.
    Keep output concise for console logs.
    """
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return raw
        code = obj.get("code")
        msg = obj.get("message")
        diag = obj.get("diag") if isinstance(obj.get("diag"), dict) else {}
        rejects = diag.get("rejects") if isinstance(diag.get("rejects"), dict) else {}
        constraints = diag.get("constraints") if isinstance(diag.get("constraints"), dict) else {}
        if rejects:
            top = sorted(rejects.items(), key=lambda kv: int(kv[1]), reverse=True)[:3]
            top_s = ", ".join([f"{k}={v}" for k, v in top])
        else:
            top_s = ""
        parts = []
        if code:
            parts.append(str(code))
        if msg:
            parts.append(str(msg))
        if top_s:
            parts.append(f"top_rejects({top_s})")
        if constraints:
            # only print the most relevant knobs
            keys = ["max_loss_rate", "max_jitter_ms", "max_delay_ms", "min_bw_mbps", "min_hops", "max_hops"]
            c = ", ".join([f"{k}={constraints.get(k)}" for k in keys if k in constraints])
            if c:
                parts.append(f"constraints({c})")
        return " | ".join(parts) if parts else raw
    except Exception:
        return raw


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class SimConfig:
    etcd_host: str
    etcd_port: int
    etcd_endpoint: str

    ds_port: int
    ds_addr: str

    target_sar_ip: str
    target_sar_port: int

    num_sors: int
    base_ip_prefix: str
    start_octet: int

    telemetry_interval_s: float
    neighbor_poll_s: float

    requests: int
    req_bandwidth: int

    # Relaxed constraints to reduce "no feasible path" in small sims.
    max_voice_delay_ms: float
    max_jitter_ms: float
    max_loss_rate: float
    min_bw_mbps: float
    min_hops: int
    max_hops: int


def load_config() -> SimConfig:
    etcd_host = os.getenv("ETCD_HOST", "127.0.0.1")
    etcd_port = _env_int("ETCD_PORT", 2379)
    ds_port = _env_int("DS_PORT", 50052)
    target_sar_ip = os.getenv("TARGET_SAR", os.getenv("SAR_IP", "8.8.8.8"))
    target_sar_port = _env_int("SERVICE_PORT", 53)

    return SimConfig(
        etcd_host=etcd_host,
        etcd_port=etcd_port,
        etcd_endpoint=f"http://{etcd_host}:{etcd_port}",
        ds_port=ds_port,
        ds_addr=f"127.0.0.1:{ds_port}",
        target_sar_ip=target_sar_ip,
        target_sar_port=target_sar_port,
        num_sors=_env_int("NUM_SORS", 12),
        base_ip_prefix=os.getenv("SOR_IP_PREFIX", "10.0.0."),
        start_octet=_env_int("SOR_START_OCTET", 101),
        telemetry_interval_s=_env_float("TELEMETRY_INTERVAL_S", 3.0),
        neighbor_poll_s=_env_float("NEIGHBOR_POLL_S", 1.0),
        requests=_env_int("REQUESTS", 20),
        req_bandwidth=_env_int("REQ_BANDWIDTH", 5),
        max_voice_delay_ms=_env_float("MAX_VOICE_DELAY_MS", 600.0),
        max_jitter_ms=_env_float("MAX_JITTER_MS", 120.0),
        max_loss_rate=_env_float("MAX_LOSS_RATE", 0.2),
        min_bw_mbps=_env_float("MIN_BW_MBPS", 1.0),
        min_hops=_env_int("MIN_HOPS", 2),
        max_hops=_env_int("MAX_HOPS", 6),
    )


CITY_COORDINATES: Dict[str, Tuple[float, float]] = {
    "beijing": (39.9042, 116.4074),
    "shanghai": (31.2304, 121.4737),
    "guangzhou": (23.1291, 113.2644),
    "shenzhen": (22.5431, 114.0579),
    "chengdu": (30.5728, 104.0668),
    "wuhan": (30.5928, 114.3055),
    "xian": (34.3416, 108.9398),
    "hangzhou": (30.2741, 120.1551),
    "hongkong": (22.3193, 114.1694),
    "singapore": (1.3521, 103.8198),
    "siliconvalley": (37.3387, -121.8853),
    "frankfurt": (50.1109, 8.6821),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    lat1r = math.radians(lat1)
    lon1r = math.radians(lon1)
    lat2r = math.radians(lat2)
    lon2r = math.radians(lon2)
    dlon = lon2r - lon1r
    dlat = lat2r - lat1r
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def _pick_city(i: int) -> str:
    names = sorted(CITY_COORDINATES.keys())
    return names[i % len(names)]


def _synth_link_metrics(src_city: str, dst_city: str) -> Tuple[float, float, float, float]:
    (lat1, lon1) = CITY_COORDINATES[src_city]
    (lat2, lon2) = CITY_COORDINATES[dst_city]
    dist = haversine_km(lat1, lon1, lat2, lon2)

    # Delay model: base + distance-based + jitter
    base_rtt = 2.0 if dist < 50 else (dist * 0.018 + 8.0)
    rtt = max(1.0, random.gauss(base_rtt, 3.0))

    # Jitter and loss: small by default, with occasional spikes
    jitter = max(0.2, random.gauss(8.0 if dist > 2000 else 3.0, 2.0))
    loss = min(0.12, max(0.0, random.gauss(0.01 if dist < 3000 else 0.03, 0.01)))

    # Bandwidth: generous but variable; correlate negatively with distance a bit
    bw = max(5.0, random.gauss(500.0 - min(250.0, dist / 20.0), 80.0))

    return (round(rtt, 2), round(jitter, 2), round(loss, 4), round(bw, 2))


class SorSimNode(threading.Thread):
    def __init__(
        self,
        *,
        etcd: etcd3.Etcd3Client,
        ip: str,
        city: str,
        telemetry_interval_s: float,
        neighbor_poll_s: float,
        stop_event: threading.Event,
        sor_logger: logging.Logger,
    ) -> None:
        super().__init__(daemon=True)
        self.etcd = etcd
        self.ip = ip
        self.city = city
        self.telemetry_interval_s = float(telemetry_interval_s)
        self.neighbor_poll_s = float(neighbor_poll_s)
        self.stop_event = stop_event
        self.sor_logger = sor_logger
        self._neighbors: List[str] = []
        self._neighbor_lock = threading.Lock()

    def run(self) -> None:
        self._bootstrap_status()

        t1 = threading.Thread(target=self._neighbor_poller, daemon=True)
        t1.start()

        next_tick = time.time()
        while not self.stop_event.is_set():
            now = time.time()
            if now < next_tick:
                time.sleep(min(0.2, next_tick - now))
                continue
            next_tick = now + self.telemetry_interval_s

            with self._neighbor_lock:
                neighbors = list(self._neighbors)

            cpu = float(max(5.0, min(95.0, random.gauss(25.0, 10.0))))
            mem = float(max(5.0, min(95.0, random.gauss(35.0, 12.0))))
            overloaded = bool(cpu > 90.0 or mem > 90.0)

            links: Dict[str, dict] = {}
            for nb in neighbors:
                # Unknown nodes fall back to synthetic mapping based on hash.
                nb_city = _pick_city(abs(hash(nb)) % 97)
                rtt, jitter, loss, bw = _synth_link_metrics(self.city, nb_city)
                links[nb] = {
                    "rtt_ms": rtt,
                    "status": "UP",
                    "bw": bw,
                    "jitter": jitter,
                    "loss": loss,
                }

            report = {
                "timestamp": time.time(),
                "node_status": {
                    "cpu_percent": cpu,
                    "mem_percent": mem,
                    "is_overloaded": overloaded,
                    "city": self.city,
                },
                "links": links,
            }
            try:
                self.sor_logger.info("telemetry ip=%s report=%s", self.ip, json.dumps(report, ensure_ascii=False))
            except Exception:
                pass
            try:
                self.etcd.put(status_key(self.ip), json.dumps(report))
            except Exception:
                # etcd hiccup; keep running
                pass

    def _bootstrap_status(self) -> None:
        try:
            self.etcd.put(
                status_key(self.ip),
                json.dumps(
                    {
                        "timestamp": time.time(),
                        "node_status": {"cpu_percent": 10.0, "mem_percent": 10.0, "is_overloaded": False, "city": self.city},
                        "links": {},
                    }
                ),
            )
        except Exception:
            pass

    def _neighbor_poller(self) -> None:
        key = neighbor_config_key(self.ip)
        while not self.stop_event.is_set():
            try:
                val, _ = self.etcd.get(key)
                if val:
                    nbs = json.loads(val.decode("utf-8"))
                    if isinstance(nbs, list):
                        with self._neighbor_lock:
                            self._neighbors = [str(x) for x in nbs]
            except Exception:
                pass
            time.sleep(self.neighbor_poll_s)


def clear_network_prefix(etcd_endpoint: str) -> None:
    subprocess.run(
        ["etcdctl", f"--endpoints={etcd_endpoint}", "del", "--prefix", "/network/"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def start_ds(*, env: Dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-u", "ds_server.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def start_sar_register(*, env: Dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-u", "sar_register.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )


def _terminate(proc: subprocess.Popen, *, name: str, timeout_s: float = 3.0) -> None:
    try:
        if proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass


def wait_for_sar_visible(etcd: etcd3.Etcd3Client, *, sar_ip: str, timeout_s: float = 20.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            v, _ = etcd.get(sar_key(sar_ip))
            if v:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def wait_for_any_link_to_target(
    etcd: etcd3.Etcd3Client,
    *,
    target_ip: str,
    timeout_s: float = 40.0,
) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            for value, meta in etcd.get_prefix("/network/status/"):
                data = json.loads(value.decode("utf-8"))
                links = data.get("links", {}) or {}
                info = links.get(target_ip)
                if isinstance(info, dict) and info.get("status") == "UP":
                    return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def reconstruct_path_from_rules(
    etcd: etcd3.Etcd3Client,
    *,
    first_hop_ip: str,
    tunnel_id: int,
    target_ip: str,
    max_steps: int = 32,
) -> List[str]:
    path = [first_hop_ip]
    cur = first_hop_ip
    seen = {cur}
    for _ in range(max_steps):
        if cur == target_ip:
            return path
        try:
            v, _ = etcd.get(rule_key(cur, tunnel_id))
            if not v:
                return path
            rule = json.loads(v.decode("utf-8"))
            nxt = str(rule.get("rip"))
            if not nxt:
                return path
            path.append(nxt)
            if nxt in seen:
                return path
            seen.add(nxt)
            cur = nxt
        except Exception:
            return path
    return path


def main() -> int:
    cfg = load_config()

    etcd = etcd3.client(host=cfg.etcd_host, port=cfg.etcd_port)
    stop_event = threading.Event()

    log_dir = _ensure_dir(os.getenv("SIM_LOG_DIR", os.path.join(REPO_ROOT, ".smoke")))
    ds_log_path = os.path.join(log_dir, "ds.log")
    sar_log_path = os.path.join(log_dir, "sar.log")
    sor_log_path = os.path.join(log_dir, "sor.log")
    smr_log_path = os.path.join(log_dir, "smr.log")
    sim_log_path = os.path.join(log_dir, "sim.log")
    sor_logger = _setup_file_logger("sim.sor", sor_log_path)
    smr_logger = _setup_file_logger("sim.smr", smr_log_path)
    sim_logger = _setup_file_logger("sim.main", sim_log_path)
    # sim.log should also be visible in console
    sh = logging.StreamHandler(stream=sys.stdout)
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    sim_logger.addHandler(sh)

    #region agent log
    _agent_log(
        "H_paths",
        "log paths and handlers configured",
        {
            "log_dir": log_dir,
            "ds_log_path": ds_log_path,
            "sar_log_path": sar_log_path,
            "sor_log_path": sor_log_path,
            "smr_log_path": smr_log_path,
            "sim_log_path": sim_log_path,
            "sim_handlers": [type(h).__name__ for h in sim_logger.handlers],
        },
    )
    #endregion agent log

    def _handle_sig(_signum: int, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    _log_line(sim_logger, "=====================================================")
    _log_line(sim_logger, "🧪 E2E Simulation: DS + SAR + simulated SOR cluster")
    _log_line(sim_logger, "=====================================================")
    _log_line(sim_logger, f"[cfg] etcd={cfg.etcd_endpoint}  ds={cfg.ds_addr}  sar={cfg.target_sar_ip}:{cfg.target_sar_port}")
    _log_line(sim_logger, f"[cfg] sors={cfg.num_sors}  requests={cfg.requests}  req_bw={cfg.req_bandwidth}")
    _log_line(sim_logger, f"[cfg] logs={log_dir} (ds.log/sar.log/sor.log/smr.log/sim.log)")

    _log_line(sim_logger, "[step] clearing /network/ prefix")
    clear_network_prefix(cfg.etcd_endpoint)

    # Start DS with relaxed constraints to ensure feasible pool in small sims.
    env = dict(os.environ)
    env.update(
        {
            "ETCD_HOST": cfg.etcd_host,
            "ETCD_PORT": str(cfg.etcd_port),
            "DS_GRPC_BIND": f"[::]:{cfg.ds_port}",
            "MAX_VOICE_DELAY_MS": str(cfg.max_voice_delay_ms),
            "MAX_JITTER_MS": str(cfg.max_jitter_ms),
            "MAX_LOSS_RATE": str(cfg.max_loss_rate),
            "MIN_BW_MBPS": str(cfg.min_bw_mbps),
            "MIN_HOPS": str(cfg.min_hops),
            "MAX_HOPS": str(cfg.max_hops),
            "RULE_TTL_S": str(_env_int("RULE_TTL_S", 90)),
        }
    )

    _log_line(sim_logger, "[step] starting DS")
    ds_proc = start_ds(env=env)
    sar_env = dict(env)
    sar_env.update({"SAR_IP": cfg.target_sar_ip, "SERVICE_PORT": str(cfg.target_sar_port)})
    _log_line(sim_logger, "[step] starting SAR register")
    sar_proc = start_sar_register(env=sar_env)

    # Append DS/SAR logs across runs; each line includes an added timestamp prefix.
    ds_fh = open(ds_log_path, "a", encoding="utf-8", newline="\n")
    sar_fh = open(sar_log_path, "a", encoding="utf-8", newline="\n")
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        ds_fh.write(f"{ts} [sim] started DS pid={ds_proc.pid}\n")
        sar_fh.write(f"{ts} [sim] started SAR pid={sar_proc.pid}\n")
        ds_fh.flush()
        sar_fh.flush()
    except Exception:
        pass
    ds_pump = None
    sar_pump = None
    if ds_proc.stdout is not None:
        ds_pump = _tee_stream_to_file(
            ds_proc.stdout,
            log_fh=ds_fh,
            prefix="DS",
            stop_event=stop_event,
            echo_to_stdout=False,
        )
    if sar_proc.stdout is not None:
        sar_pump = _tee_stream_to_file(
            sar_proc.stdout,
            log_fh=sar_fh,
            prefix="SAR",
            stop_event=stop_event,
            echo_to_stdout=False,
        )

    #region agent log
    _agent_log(
        "H_streams",
        "subprocess stream pump started",
        {
            "ds_stdout": bool(ds_proc.stdout is not None),
            "sar_stdout": bool(sar_proc.stdout is not None),
            "ds_fh_mode": getattr(ds_fh, "mode", None),
            "sar_fh_mode": getattr(sar_fh, "mode", None),
            "ds_pid": getattr(ds_proc, "pid", None),
            "sar_pid": getattr(sar_proc, "pid", None),
        },
    )
    #endregion agent log

    try:
        if not wait_for_sar_visible(etcd, sar_ip=cfg.target_sar_ip, timeout_s=15.0):
            _log_line(sim_logger, "[warn] SAR not visible in etcd yet (continuing)")

        # Spawn SOR sims
        _log_line(sim_logger, "[step] spawning simulated SOR nodes")
        sors: List[SorSimNode] = []
        for i in range(cfg.num_sors):
            ip = f"{cfg.base_ip_prefix}{cfg.start_octet + i}"
            city = _pick_city(i)
            t = SorSimNode(
                etcd=etcd,
                ip=ip,
                city=city,
                telemetry_interval_s=cfg.telemetry_interval_s,
                neighbor_poll_s=cfg.neighbor_poll_s,
                stop_event=stop_event,
                sor_logger=sor_logger,
            )
            t.start()
            sors.append(t)
            time.sleep(0.2)

        _log_line(sim_logger, "[step] waiting for at least one link to SAR target")
        if not wait_for_any_link_to_target(etcd, target_ip=cfg.target_sar_ip, timeout_s=50.0):
            _log_line(sim_logger, "[fail] no SOR reported UP link to SAR in time; routing may fail")

        # Issue multiple SMR requests and summarize chosen paths.
        _log_line(sim_logger, "[step] issuing SMR RequestTunnel calls")
        channel = grpc.insecure_channel(cfg.ds_addr)
        stub = control_pb2_grpc.DirectoryServerStub(channel)

        hop_hist: Dict[int, int] = {}
        seen_paths: Dict[str, int] = {}
        failures = 0

        for i in range(cfg.requests):
            if stop_event.is_set():
                break
            req = control_pb2.TunnelReq(smr_id=f"sim-{i}", target_sar_ip=cfg.target_sar_ip, req_bandwidth=cfg.req_bandwidth)
            try:
                resp = stub.RequestTunnel(req, timeout=5.0)
            except grpc.RpcError as e:
                failures += 1
                _log_line(sim_logger, f"[req {i:02d}] rpc error: {e.details()}")
                smr_logger.info("req=%02d status=rpc_error details=%s", i, e.details())
                time.sleep(0.6)
                continue

            if not resp.success:
                failures += 1
                msg = _format_reject_message(resp.message)
                _log_line(sim_logger, f"[req {i:02d}] reject: {msg}")
                smr_logger.info("req=%02d status=reject message=%s", i, msg)
                time.sleep(0.6)
                continue

            path = reconstruct_path_from_rules(
                etcd,
                first_hop_ip=resp.first_hop_ip,
                tunnel_id=int(resp.tunnel_id),
                target_ip=cfg.target_sar_ip,
            )
            hops = max(0, len(path) - 1)
            hop_hist[hops] = hop_hist.get(hops, 0) + 1
            key = " -> ".join(path)
            seen_paths[key] = seen_paths.get(key, 0) + 1
            _log_line(
                sim_logger,
                f"[req {i:02d}] tunnel={resp.tunnel_id} first={resp.first_hop_ip}:{resp.first_hop_port} hops={hops} path={key}",
            )
            smr_logger.info(
                "req=%02d status=ok tunnel=%s first=%s:%s hops=%d path=%s",
                i,
                resp.tunnel_id,
                resp.first_hop_ip,
                resp.first_hop_port,
                hops,
                key,
            )
            time.sleep(0.6)

        _log_line(sim_logger, "-----------------------------------------------------")
        _log_line(sim_logger, "[summary] hop histogram (edges):")
        for h in sorted(hop_hist.keys()):
            _log_line(sim_logger, f"  hops={h}: {hop_hist[h]}")
        _log_line(sim_logger, f"[summary] unique paths: {len(seen_paths)}  failures/rejects: {failures}")
        top = sorted(seen_paths.items(), key=lambda kv: kv[1], reverse=True)[:10]
        if top:
            _log_line(sim_logger, "[summary] top paths:")
            for p, c in top:
                _log_line(sim_logger, f"  x{c}: {p}")

        return 0 if failures < max(1, cfg.requests // 2) else 2

    finally:
        stop_event.set()
        _terminate(sar_proc, name="sar")
        _terminate(ds_proc, name="ds")
        try:
            if ds_proc.stdout:
                ds_proc.stdout.close()
        except Exception:
            pass
        try:
            if sar_proc.stdout:
                sar_proc.stdout.close()
        except Exception:
            pass
        try:
            ds_fh.close()
        except Exception:
            pass
        try:
            sar_fh.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

