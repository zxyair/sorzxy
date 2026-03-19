from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Dict, Optional


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

        proc = subprocess.Popen(cmd)
        self._active[spec.tunnel_id] = proc
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

