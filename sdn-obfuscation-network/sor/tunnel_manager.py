from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Dict

_DEBUG_LOG_PATH = "/home/ubuntu/sorzxy/.cursor/debug-92d48c.log"
_DEBUG_SESSION_ID = "92d48c"


def _debug_ndjson_log(*, hypothesisId: str, runId: str, location: str, message: str, data: dict) -> None:
    """
    Write one NDJSON line to the debug log file for runtime evidence.
    """
    payload = {
        "sessionId": _DEBUG_SESSION_ID,
        "runId": runId,
        "hypothesisId": hypothesisId,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    # #region tunnel_manager NDJSON debug log
    with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    # #endregion

@dataclass(frozen=True)
class TunnelSpec:
    tunnel_id: int
    listen_port: int
    remote_ip: str
    remote_port: int
    udp: bool = True


class TunnelManager:
    def __init__(self, mapper_path: str):
        self._mapper_path = mapper_path
        self._active: Dict[int, subprocess.Popen] = {}

    def is_active(self, tunnel_id: int) -> bool:
        proc = self._active.get(tunnel_id)
        return proc is not None and proc.poll() is None

    def apply(self, spec: TunnelSpec) -> subprocess.Popen:
        """
        Start or hot-restart a tunnel.
        """
        self.stop(spec.tunnel_id)

        cmd = [
            self._mapper_path,
            "-l",
            f"0.0.0.0:{spec.listen_port}",
            "-r",
            f"{spec.remote_ip}:{spec.remote_port}",
        ]
        cmd.append("-u" if spec.udp else "-t")

        # #region tunnel_manager apply pre-start debug log
        _debug_ndjson_log(
            hypothesisId="H1_udp_only_vs_iperf3_tcp_control",
            runId="pre-fix",
            location="tunnel_manager.py:apply",
            message="starting tinymapper",
            data={
                "tunnel_id": spec.tunnel_id,
                "listen": f"0.0.0.0:{spec.listen_port}",
                "remote": f"{spec.remote_ip}:{spec.remote_port}",
                "udp_flag": spec.udp,
                "cmd": cmd,
            },
        )
        # #endregion

        proc = subprocess.Popen(cmd)
        self._active[spec.tunnel_id] = proc

        # #region tunnel_manager apply post-start debug log
        _debug_ndjson_log(
            hypothesisId="H3_proc_died_or_failed_bind",
            runId="pre-fix",
            location="tunnel_manager.py:apply",
            message="tinymapper started (immediate poll)",
            data={"tunnel_id": spec.tunnel_id, "pid": proc.pid, "poll": proc.poll()},
        )
        # #endregion

        return proc

    def stop(self, tunnel_id: int, timeout_s: float = 2.0) -> bool:
        proc = self._active.get(tunnel_id)
        if proc is None:
            return False

        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    proc.kill()
        finally:
            self._active.pop(tunnel_id, None)
        return True

    def stop_all(self) -> None:
        for tid in list(self._active.keys()):
            self.stop(tid)
        # tinyPortMapper is stateless; give OS a brief moment to release ports.
        time.sleep(0.05)

