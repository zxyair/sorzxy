#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

import matplotlib.pyplot as plt


BASE = Path(__file__).resolve().parent
INPUT_JSONL = BASE / "path_quality_diversity_results.jsonl"
OUT_DIR = BASE / "figures_path"

# Must align with ds_config.json / runtime settings
QOS_DELAY_MS = 250.0


def load_delays_ms() -> List[float]:
    delays: List[float] = []
    for line in INPUT_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if not r.get("ok"):
            continue
        qos = r.get("qos_observed") or {}
        d = qos.get("path_delay_ms")
        if d is None:
            continue
        delays.append(float(d))
    return delays


def ecdf(values: List[float]) -> Tuple[List[float], List[float]]:
    x = sorted(values)
    n = len(x)
    y = [(i + 1) / n for i in range(n)]
    return x, y


def percentile(sorted_values: List[float], p: float) -> float:
    if not sorted_values:
        raise ValueError("empty values")
    p = max(0.0, min(1.0, float(p)))
    idx = int(round(p * (len(sorted_values) - 1)))
    idx = max(0, min(len(sorted_values) - 1, idx))
    return float(sorted_values[idx])


def main() -> None:
    delays = load_delays_ms()
    if not delays:
        raise RuntimeError(f"No delays found in {INPUT_JSONL} (check qos_observed.path_delay_ms).")

    x, y = ecdf(delays)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_png = OUT_DIR / "fig13_delay_ecdf.png"
    out_pdf = OUT_DIR / "fig13_delay_ecdf.pdf"

    fig, ax = plt.subplots(figsize=(8.8, 4.8))
    ax.plot(x, y, linewidth=2.2)
    ax.axvline(
        QOS_DELAY_MS,
        linestyle="--",
        linewidth=1.4,
        color="black",
        label=f"QoS threshold = {QOS_DELAY_MS:.0f} ms",
    )
    ax.set_title("ECDF of End-to-End Path Delay")
    ax.set_xlabel("End-to-End Delay (ms)")
    ax.set_ylabel("Cumulative Probability")
    ax.grid(alpha=0.25)
    ax.set_ylim(0, 1.02)
    ax.legend(frameon=False, loc="lower right")

    fig.tight_layout()
    fig.savefig(out_png, dpi=300)
    fig.savefig(out_pdf)
    plt.close(fig)

    delays_sorted = sorted(delays)
    ok = sum(1 for d in delays_sorted if d <= QOS_DELAY_MS)
    rate = ok / len(delays_sorted)

    stats = {
        "count": len(delays_sorted),
        "min_ms": min(delays_sorted),
        "median_ms": percentile(delays_sorted, 0.5),
        "p90_ms": percentile(delays_sorted, 0.9),
        "p95_ms": percentile(delays_sorted, 0.95),
        "max_ms": max(delays_sorted),
        "leq_threshold_rate": rate,
        "threshold_ms": QOS_DELAY_MS,
        "out_png": str(out_png),
        "out_pdf": str(out_pdf),
    }
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

