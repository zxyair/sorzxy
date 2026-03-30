#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import matplotlib.pyplot as plt
import numpy as np


BASE_DIR = Path(__file__).resolve().parent
INPUT_FILE = BASE_DIR / "first_hop_results.jsonl"
OUT_DIR = BASE_DIR / "figures"


def load_ok_rows(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if obj.get("ok"):
            rows.append(obj)
    rows.sort(key=lambda x: x["idx"])
    return rows


def save_fig(fig: plt.Figure, stem: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT_DIR / f"{stem}.png", dpi=300)
    fig.savefig(OUT_DIR / f"{stem}.pdf")
    plt.close(fig)


def fig1_node_distribution(rows: List[Dict[str, Any]]) -> None:
    counts: Dict[str, int] = {}
    for r in rows:
        ip = r["first_hop_ip"]
        counts[ip] = counts.get(ip, 0) + 1
    pairs = sorted(counts.items(), key=lambda x: x[1], reverse=True)

    labels = [p[0] for p in pairs]
    values = [p[1] for p in pairs]

    fig, ax = plt.subplots(figsize=(8.8, 4.8))
    ax.bar(labels, values)
    ax.set_title("Distribution of Selected First-hop SOR Nodes")
    ax.set_xlabel("First-hop SOR Node (IP)")
    ax.set_ylabel("Request Count")
    ax.grid(axis="y", alpha=0.25)
    ax.tick_params(axis="x", rotation=25)
    save_fig(fig, "fig1_first_hop_node_distribution")


def fig2_port_scatter(rows: List[Dict[str, Any]], port_min: int = 10000, port_max: int = 60000) -> None:
    x = [r["idx"] for r in rows]
    y = [r["first_hop_port"] for r in rows]

    fig, ax = plt.subplots(figsize=(9.2, 4.8))
    ax.scatter(x, y, s=18, alpha=0.85)
    ax.axhline(port_min, linestyle="--", linewidth=1.0, label=f"port_min={port_min}")
    ax.axhline(port_max, linestyle="--", linewidth=1.0, label=f"port_max={port_max}")
    ax.set_title("Assigned First-hop Ports across Concurrent Requests")
    ax.set_xlabel("Request Index")
    ax.set_ylabel("Assigned First-hop Port")
    ax.grid(alpha=0.25)
    ax.legend(frameon=False, loc="lower right")
    save_fig(fig, "fig2_first_hop_port_scatter")


def fig3_port_hist(rows: List[Dict[str, Any]], bins: int = 40) -> None:
    ports = [r["first_hop_port"] for r in rows]

    fig, ax = plt.subplots(figsize=(8.8, 4.8))
    ax.hist(ports, bins=bins, edgecolor="white", linewidth=0.6)
    ax.set_title("Histogram of Assigned First-hop Ports")
    ax.set_xlabel("First-hop Port")
    ax.set_ylabel("Frequency")
    ax.grid(axis="y", alpha=0.25)
    save_fig(fig, "fig3_first_hop_port_histogram")


def fig4_heatmap(rows: List[Dict[str, Any]], bucket_size: int = 2000) -> None:
    nodes = sorted({r["first_hop_ip"] for r in rows})
    ports = [int(r["first_hop_port"]) for r in rows]
    pmin, pmax = min(ports), max(ports)

    start = (pmin // bucket_size) * bucket_size
    end = ((pmax + bucket_size - 1) // bucket_size) * bucket_size
    edges = list(range(start, end + bucket_size, bucket_size))
    bucket_labels = [f"{edges[i]}-{edges[i+1]-1}" for i in range(len(edges) - 1)]

    node_to_idx = {n: i for i, n in enumerate(nodes)}
    mat = np.zeros((len(nodes), len(bucket_labels)), dtype=int)

    for r in rows:
        ni = node_to_idx[r["first_hop_ip"]]
        p = int(r["first_hop_port"])
        bi = min((p - start) // bucket_size, len(bucket_labels) - 1)
        mat[ni, bi] += 1

    fig, ax = plt.subplots(figsize=(11.0, 4.8))
    im = ax.imshow(mat, aspect="auto")
    ax.set_title("Heatmap of First-hop Node vs. Port Bucket")
    ax.set_xlabel("Port Bucket")
    ax.set_ylabel("First-hop SOR Node (IP)")
    ax.set_yticks(range(len(nodes)))
    ax.set_yticklabels(nodes)

    step = max(1, len(bucket_labels) // 8)
    xticks = list(range(0, len(bucket_labels), step))
    ax.set_xticks(xticks)
    ax.set_xticklabels([bucket_labels[i] for i in xticks], rotation=25, ha="right")
    fig.colorbar(im, ax=ax, label="Request Count")
    save_fig(fig, "fig4_node_port_bucket_heatmap")


def fig5_latency_ecdf(rows: List[Dict[str, Any]]) -> None:
    lat = sorted(float(r["lat_ms"]) for r in rows if r.get("lat_ms") is not None)
    n = len(lat)
    y = [(i + 1) / n for i in range(n)]

    fig, ax = plt.subplots(figsize=(8.8, 4.8))
    ax.plot(lat, y, linewidth=2.0)
    ax.set_title("ECDF of Tunnel Setup Latency")
    ax.set_xlabel("Setup Latency (ms)")
    ax.set_ylabel("Cumulative Probability")
    ax.grid(alpha=0.25)
    save_fig(fig, "fig5_latency_ecdf")


def main() -> None:
    rows = load_ok_rows(INPUT_FILE)
    if not rows:
        raise RuntimeError(f"No successful records in: {INPUT_FILE}")

    fig1_node_distribution(rows)
    fig2_port_scatter(rows)
    fig3_port_hist(rows)
    fig4_heatmap(rows)
    fig5_latency_ecdf(rows)

    print(f"Generated figures under: {OUT_DIR}")
    print("Files:")
    for p in sorted(OUT_DIR.glob("*")):
        print(f"  - {p.name}")


if __name__ == "__main__":
    main()
