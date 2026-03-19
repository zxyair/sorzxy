from __future__ import annotations

from dataclasses import dataclass
from typing import Deque, Dict, Optional
from collections import deque
import math


@dataclass(frozen=True)
class LinkQosSnapshot:
    """
    Derived QoS metrics for a neighbor based on recent ping samples.
    """

    rtt_ms: float
    loss: float  # 0..1
    jitter_ms: float


class LinkQosWindow:
    """
    Sliding window of ping samples for a single neighbor.

    We store RTT samples (ms) for successes and None for losses.
    """

    def __init__(self, window_size: int):
        self._window_size = max(1, int(window_size))
        self._samples: Deque[Optional[float]] = deque(maxlen=self._window_size)

    def add_success(self, rtt_ms: float) -> None:
        self._samples.append(float(rtt_ms))

    def add_loss(self) -> None:
        self._samples.append(None)

    def snapshot(self, *, min_samples: int) -> Optional[LinkQosSnapshot]:
        if len(self._samples) < max(1, int(min_samples)):
            return None

        total = len(self._samples)
        loss_count = sum(1 for s in self._samples if s is None)
        ok_rtts = [s for s in self._samples if isinstance(s, (int, float))]

        # If everything is lost, expose extreme values.
        if not ok_rtts:
            return LinkQosSnapshot(rtt_ms=9999.0, loss=1.0, jitter_ms=0.0)

        mean_rtt = sum(ok_rtts) / len(ok_rtts)

        # Jitter: standard deviation over successful RTT samples.
        # (simple & stable; later we can switch to p95-p50)
        if len(ok_rtts) >= 2:
            var = sum((x - mean_rtt) ** 2 for x in ok_rtts) / (len(ok_rtts) - 1)
            jitter = math.sqrt(max(0.0, var))
        else:
            jitter = 0.0

        return LinkQosSnapshot(
            rtt_ms=mean_rtt,
            loss=float(loss_count) / float(total),
            jitter_ms=jitter,
        )


class QosWindowStats:
    def __init__(self, *, window_size: int, min_samples: int):
        self._window_size = int(window_size)
        self._min_samples = int(min_samples)
        self._windows: Dict[str, LinkQosWindow] = {}

    def add_success(self, neighbor_ip: str, rtt_ms: float) -> None:
        self._get(neighbor_ip).add_success(rtt_ms)

    def add_loss(self, neighbor_ip: str) -> None:
        self._get(neighbor_ip).add_loss()

    def get_snapshot(self, neighbor_ip: str) -> Optional[LinkQosSnapshot]:
        w = self._windows.get(neighbor_ip)
        if w is None:
            return None
        return w.snapshot(min_samples=self._min_samples)

    def _get(self, neighbor_ip: str) -> LinkQosWindow:
        w = self._windows.get(neighbor_ip)
        if w is None:
            w = LinkQosWindow(window_size=self._window_size)
            self._windows[neighbor_ip] = w
        return w

