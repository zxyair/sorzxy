#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

import matplotlib.pyplot as plt
import numpy as np


BASE = Path(__file__).resolve().parent
INPUT_JSONL = BASE / "path_quality_diversity_results.jsonl"
SUMMARY_JSON = BASE / "path_quality_diversity_summary.json"
OUT_DIR = BASE / "figures_path"


def load_rows(path: Path) -> List[Dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def save(fig: plt.Figure, stem: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(OUT_DIR / f"{stem}.png", dpi=300)
    fig.savefig(OUT_DIR / f"{stem}.pdf")
    plt.close(fig)


def levenshtein(a: List[str], b: List[str]) -> int:
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    dp = list(range(m + 1))
    for i in range(1, n + 1):
        prev = dp[0]
        dp[0] = i
        for j in range(1, m + 1):
            cur = dp[j]
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = cur
    return dp[m]


def fig_qos_compliance(summary: Dict[str, Any]) -> None:
    dim = summary["qos_compliance"]["dimension_pass_rate"]
    labels = list(dim.keys()) + ["overall"]
    values = [dim[k] for k in dim.keys()] + [summary["qos_compliance"]["overall_compliance_rate"]]

    fig, ax = plt.subplots(figsize=(8.8, 4.8))
    ax.bar(labels, values)
    ax.set_ylim(0, 1.05)
    ax.set_title("Path QoS Compliance Rate")
    ax.set_xlabel("QoS Dimension")
    ax.set_ylabel("Pass Rate")
    ax.grid(axis="y", alpha=0.25)
    save(fig, "fig6_path_qos_compliance_rate")


def fig_jaccard_distribution(paths: List[List[str]]) -> None:
    edge_sets = [set([f"{p[i]}->{p[i+1]}" for i in range(len(p) - 1)]) for p in paths]
    vals = []
    for i in range(len(edge_sets)):
        for j in range(i + 1, len(edge_sets)):
            a, b = edge_sets[i], edge_sets[j]
            union = a | b
            if union:
                vals.append(len(a & b) / len(union))
    fig, ax = plt.subplots(figsize=(8.8, 4.8))
    ax.hist(vals, bins=40, edgecolor="white", linewidth=0.6)
    ax.set_title("Pairwise Path Jaccard Similarity Distribution")
    ax.set_xlabel("Jaccard Similarity")
    ax.set_ylabel("Frequency")
    ax.grid(axis="y", alpha=0.25)
    save(fig, "fig7_pairwise_path_jaccard_distribution")


def fig_norm_edit_distribution(paths: List[List[str]]) -> None:
    vals = []
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            d = levenshtein(paths[i], paths[j])
            vals.append(d / max(len(paths[i]), len(paths[j]), 1))
    fig, ax = plt.subplots(figsize=(8.8, 4.8))
    ax.hist(vals, bins=40, edgecolor="white", linewidth=0.6)
    ax.set_title("Pairwise Normalized Edit Distance Distribution")
    ax.set_xlabel("Normalized Edit Distance")
    ax.set_ylabel("Frequency")
    ax.grid(axis="y", alpha=0.25)
    save(fig, "fig8_pairwise_norm_edit_distance_distribution")


def fig_path_frequency_entropy(paths: List[List[str]]) -> None:
    sigs = ["->".join(p) for p in paths]
    counter = Counter(sigs)
    top = counter.most_common(10)
    labels = [x[0] for x in top]
    values = [x[1] for x in top]
    probs = [v / len(sigs) for v in counter.values()] if sigs else []
    entropy = -sum(p * math.log2(p) for p in probs) if probs else 0.0

    fig, ax = plt.subplots(figsize=(11.8, 5.0))
    ax.barh(range(len(labels)), values)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_title("Path Frequency and Entropy")
    ax.set_xlabel("Frequency")
    ax.set_ylabel("Path Signature")
    ax.grid(axis="x", alpha=0.25)
    ax.text(0.98, 0.02, f"Entropy={entropy:.3f} bits", transform=ax.transAxes, ha="right", va="bottom")
    save(fig, "fig9_path_frequency_and_entropy")


def build_pairwise_metrics(paths: List[List[str]]) -> Tuple[List[float], List[float]]:
    edge_sets = [set([f"{p[i]}->{p[i+1]}" for i in range(len(p) - 1)]) for p in paths]
    jaccard_vals: List[float] = []
    edit_vals: List[float] = []
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            a, b = edge_sets[i], edge_sets[j]
            union = a | b
            if union:
                jaccard_vals.append(len(a & b) / len(union))
            d = levenshtein(paths[i], paths[j])
            edit_vals.append(d / max(len(paths[i]), len(paths[j]), 1))
    return jaccard_vals, edit_vals


def fig_pairwise_similarity_heatmap(paths: List[List[str]], max_paths: int = 120) -> None:
    # Limit N to keep the figure legible and bounded.
    sampled = paths[:max_paths]
    n = len(sampled)
    edge_sets = [set([f"{p[i]}->{p[i+1]}" for i in range(len(p) - 1)]) for p in sampled]
    mat = np.zeros((n, n), dtype=float)
    for i in range(n):
        for j in range(n):
            if i == j:
                mat[i, j] = 1.0
                continue
            union = edge_sets[i] | edge_sets[j]
            mat[i, j] = (len(edge_sets[i] & edge_sets[j]) / len(union)) if union else 0.0

    fig, ax = plt.subplots(figsize=(6.4, 5.8))
    im = ax.imshow(mat, aspect="auto", vmin=0.0, vmax=1.0)
    ax.set_title("Pairwise Path Jaccard Similarity Heatmap")
    ax.set_xlabel("Path Sample Index")
    ax.set_ylabel("Path Sample Index")
    fig.colorbar(im, ax=ax, label="Jaccard Similarity")
    save(fig, "fig10_pairwise_similarity_heatmap")


def _ecdf(vals: List[float]) -> Tuple[List[float], List[float]]:
    v = sorted(vals)
    n = len(v)
    if n == 0:
        return [], []
    y = [(i + 1) / n for i in range(n)]
    return v, y


def fig_pairwise_metric_ecdf(jaccard_vals: List[float], edit_vals: List[float]) -> None:
    x1, y1 = _ecdf(jaccard_vals)
    x2, y2 = _ecdf(edit_vals)
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.5))

    axes[0].plot(x1, y1, linewidth=2.0)
    axes[0].set_title("ECDF of Pairwise Jaccard Similarity")
    axes[0].set_xlabel("Jaccard Similarity")
    axes[0].set_ylabel("Cumulative Probability")
    axes[0].grid(alpha=0.25)

    axes[1].plot(x2, y2, linewidth=2.0)
    axes[1].set_title("ECDF of Pairwise Normalized Edit Distance")
    axes[1].set_xlabel("Normalized Edit Distance")
    axes[1].set_ylabel("Cumulative Probability")
    axes[1].grid(alpha=0.25)

    save(fig, "fig11_pairwise_metric_ecdf")


def fig_hop_transition_alluvial(paths: List[List[str]], topn_per_hop: int = 6) -> None:
    # Collect hop-layer nodes and transitions.
    max_hops = max((len(p) - 1 for p in paths), default=0)
    layer_nodes: Dict[int, Counter] = {i: Counter() for i in range(max_hops + 1)}
    transitions: Counter = Counter()

    for p in paths:
        for i, node in enumerate(p):
            layer_nodes[i][node] += 1
        for i in range(len(p) - 1):
            transitions[(i, p[i], p[i + 1])] += 1

    # Keep top nodes per layer for readability; others collapse into "Other".
    display_nodes: Dict[int, List[str]] = {}
    for layer, counter in layer_nodes.items():
        top = [n for n, _ in counter.most_common(topn_per_hop)]
        if len(counter) > topn_per_hop:
            top.append("Other")
        display_nodes[layer] = top

    def map_node(layer: int, node: str) -> str:
        return node if node in display_nodes[layer] else "Other"

    # Re-aggregate transitions after collapsing.
    agg_transitions: Counter = Counter()
    for (layer, a, b), c in transitions.items():
        agg_transitions[(layer, map_node(layer, a), map_node(layer + 1, b))] += c

    # Coordinates.
    x_positions = {layer: layer for layer in range(max_hops + 1)}
    y_positions: Dict[Tuple[int, str], float] = {}
    for layer in range(max_hops + 1):
        nodes = display_nodes[layer]
        for i, node in enumerate(nodes):
            y_positions[(layer, node)] = i

    fig, ax = plt.subplots(figsize=(12.5, 5.2))

    # Draw nodes.
    for layer in range(max_hops + 1):
        nodes = display_nodes[layer]
        for node in nodes:
            x = x_positions[layer]
            y = y_positions[(layer, node)]
            ax.scatter([x], [y], s=120, zorder=3)
            ax.text(x, y + 0.12, node, ha="center", va="bottom", fontsize=8)

    # Draw flows.
    max_count = max(agg_transitions.values()) if agg_transitions else 1
    for (layer, a, b), c in agg_transitions.items():
        x0 = x_positions[layer]
        x1 = x_positions[layer + 1]
        y0 = y_positions[(layer, a)]
        y1 = y_positions[(layer + 1, b)]
        lw = 0.7 + 6.0 * (c / max_count)
        ax.plot([x0, x1], [y0, y1], linewidth=lw, alpha=0.35, zorder=1)

    ax.set_title("Hop Transition Alluvial View (Top Nodes per Hop)")
    ax.set_xlabel("Hop Layer")
    ax.set_ylabel("Node Rank in Layer")
    ax.set_xticks(list(x_positions.values()))
    ax.set_xticklabels([f"Hop {i}" for i in range(max_hops + 1)])
    ax.grid(axis="x", alpha=0.2)
    save(fig, "fig12_hop_transition_alluvial")


def main() -> None:
    rows = load_rows(INPUT_JSONL)
    summary = json.loads(SUMMARY_JSON.read_text(encoding="utf-8"))
    rec = [r for r in rows if r.get("ok") and r.get("path_reconstructed")]
    paths = [r["selected_path"] for r in rec if isinstance(r.get("selected_path"), list)]

    fig_qos_compliance(summary)
    fig_jaccard_distribution(paths)
    fig_norm_edit_distribution(paths)
    fig_path_frequency_entropy(paths)
    jaccard_vals, edit_vals = build_pairwise_metrics(paths)
    fig_pairwise_similarity_heatmap(paths)
    fig_pairwise_metric_ecdf(jaccard_vals, edit_vals)
    fig_hop_transition_alluvial(paths)

    print(f"Generated figures under: {OUT_DIR}")
    for p in sorted(OUT_DIR.glob("*")):
        print(f"  - {p.name}")


if __name__ == "__main__":
    main()
