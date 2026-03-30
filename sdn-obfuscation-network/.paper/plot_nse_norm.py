#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib.pyplot as plt


BASE = Path(__file__).resolve().parent
INPUT_JSONL = BASE / "path_quality_diversity_results.jsonl"
OUT_DIR = BASE / "figures_path"
OUT_SUMMARY = BASE / "nse_norm_summary.json"


def load_paths() -> List[List[str]]:
    rows = []
    for line in INPUT_JSONL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if not r.get("ok") or not r.get("path_reconstructed"):
            continue
        p = r.get("selected_path")
        if isinstance(p, list) and len(p) >= 2:
            rows.append(p)
    return rows


def path_internal_nodes(path: List[str]) -> List[str]:
    # selected_path format in our dataset: [first_hop_sor, ..., sar_ip]
    # Path-internal node metric counts all SOR nodes in the path, excluding the terminal SAR.
    if len(path) < 2:
        return []
    sor_nodes = path[:-1]
    # Defensive: de-duplicate within one path to avoid loops inflating counts.
    # Keep order stable (important for per-window incremental stats).
    seen = set()
    out = []
    for n in sor_nodes:
        if n not in seen:
            out.append(str(n))
            seen.add(n)
    return out


def shannon_entropy_bits(p: List[float]) -> float:
    h = 0.0
    for x in p:
        if x <= 0:
            continue
        h -= x * math.log2(x)
    return h


def nse_norm_from_counts(counts: Counter) -> Tuple[float, float, int]:
    # Returns: (NSE_bits, NSE_norm, K)
    K = len(counts)
    if K <= 1:
        return 0.0, 0.0, K
    total = sum(counts.values())
    probs = [c / total for c in counts.values()]
    nse = shannon_entropy_bits(probs)
    nse_norm = nse / math.log2(K)
    return nse, nse_norm, K


def gini(values: List[float]) -> float:
    # Standard Gini for non-negative values.
    if not values:
        return 0.0
    v = sorted(max(0.0, float(x)) for x in values)
    n = len(v)
    s = sum(v)
    if s == 0:
        return 0.0
    cum = 0.0
    for i, x in enumerate(v, start=1):
        cum += i * x
    return (2 * cum) / (n * s) - (n + 1) / n


def save(fig: plt.Figure, stem: str) -> Tuple[str, str]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    png = OUT_DIR / f"{stem}.png"
    pdf = OUT_DIR / f"{stem}.pdf"
    fig.tight_layout()
    fig.savefig(png, dpi=300)
    fig.savefig(pdf)
    plt.close(fig)
    return str(png), str(pdf)


def plot_node_probability_bar(counts: Counter, topn: int = 15) -> Tuple[str, str]:
    total = sum(counts.values()) or 1
    items = counts.most_common(topn)
    labels = [k for k, _ in items]
    probs = [v / total for _, v in items]
    other = total - sum(v for _, v in items)
    if other > 0:
        labels.append("Other")
        probs.append(other / total)

    fig, ax = plt.subplots(figsize=(10.8, 4.8))
    ax.bar(labels, probs)
    ax.set_title("Node Selection Probability (Path-Internal Nodes)")
    ax.set_xlabel("SOR Node (IP)")
    ax.set_ylabel("Selection Probability")
    ax.grid(axis="y", alpha=0.25)
    ax.tick_params(axis="x", rotation=25)
    return save(fig, "fig14_node_selection_probability_bar")


def plot_nse_norm_over_time(paths: List[List[str]], window: int = 50) -> Tuple[str, str]:
    # Rolling window over request index: compute counts within each window and NSE_norm.
    series = []
    xs = []
    for i in range(len(paths)):
        if i + 1 < window:
            continue
        w_paths = paths[i + 1 - window : i + 1]
        c = Counter()
        for p in w_paths:
            for n in path_internal_nodes(p):
                c[n] += 1
        _, nn, K = nse_norm_from_counts(c)
        series.append(nn)
        xs.append(i + 1)

    fig, ax = plt.subplots(figsize=(10.8, 4.8))
    ax.plot(xs, series, linewidth=2.0)
    ax.set_ylim(0, 1.02)
    ax.set_title("Normalized Node Selection Entropy over Time (Rolling Window)")
    ax.set_xlabel("Request Index")
    ax.set_ylabel("NSE_norm")
    ax.grid(alpha=0.25)
    return save(fig, "fig15_nse_norm_over_time")


def plot_lorenz_curve(counts: Counter) -> Tuple[str, str]:
    vals = list(counts.values())
    vals = sorted(vals)
    n = len(vals)
    if n == 0:
        vals = [0.0]
        n = 1
    total = sum(vals) or 1.0

    cum = [0.0]
    s = 0.0
    for x in vals:
        s += x
        cum.append(s / total)
    xs = [i / n for i in range(0, n + 1)]

    fig, ax = plt.subplots(figsize=(6.8, 5.2))
    ax.plot(xs, cum, linewidth=2.2, label="Lorenz curve")
    ax.plot([0, 1], [0, 1], linestyle="--", linewidth=1.2, label="Perfect equality")
    ax.set_title("Lorenz Curve of Node Selection Frequency")
    ax.set_xlabel("Cumulative Share of Nodes")
    ax.set_ylabel("Cumulative Share of Selections")
    ax.grid(alpha=0.25)
    ax.legend(frameon=False, loc="lower right")
    return save(fig, "fig16_lorenz_curve_node_selection")


def main() -> None:
    paths = load_paths()
    if not paths:
        raise RuntimeError(f"No paths found in {INPUT_JSONL}")

    counts = Counter()
    for p in paths:
        for n in path_internal_nodes(p):
            counts[n] += 1

    nse, nse_norm, K = nse_norm_from_counts(counts)
    total_sel = sum(counts.values())
    g = gini(list(counts.values()))

    fig14_png, fig14_pdf = plot_node_probability_bar(counts)
    fig15_png, fig15_pdf = plot_nse_norm_over_time(paths, window=min(50, max(10, len(paths) // 8)))
    fig16_png, fig16_pdf = plot_lorenz_curve(counts)

    summary: Dict[str, object] = {
        "dataset": {
            "paths_count": len(paths),
            "total_node_selections": total_sel,
            "unique_nodes_K": K,
        },
        "nse": {
            "NSE_bits": nse,
            "NSE_norm": nse_norm,
            "K": K,
            "normalizer_log2K": (math.log2(K) if K > 0 else None),
        },
        "inequality": {
            "gini": g,
            "top_nodes": counts.most_common(10),
        },
        "figures": {
            "fig14_node_selection_probability_bar": {"png": fig14_png, "pdf": fig14_pdf},
            "fig15_nse_norm_over_time": {"png": fig15_png, "pdf": fig15_pdf},
            "fig16_lorenz_curve_node_selection": {"png": fig16_png, "pdf": fig16_pdf},
        },
    }

    OUT_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

