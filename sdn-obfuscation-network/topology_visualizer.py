from __future__ import annotations

import argparse
import json
import os
import platform
import time
from typing import Dict, Set, Tuple

import etcd3
import matplotlib.animation as animation
import matplotlib as mpl
import matplotlib.pyplot as plt
import networkx as nx


# #region agent log
def _debug_log(hypothesis_id: str, message: str, data: dict | None = None, run_id: str = "pre-fix") -> None:
    try:
        payload = {
            "sessionId": "4807d4",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": "topology_visualizer.py:agentlog",
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        os.makedirs("/home/ubuntu/sorzxy/.cursor", exist_ok=True)
        with open("/home/ubuntu/sorzxy/.cursor/debug-4807d4.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


_debug_log(
    "H_env",
    "process start",
    {"platform": platform.platform(), "os_name": os.name, "display": os.getenv("DISPLAY"), "backend": mpl.get_backend()},
)
# #endregion agent log


def _load_json_config_defaults() -> dict:
    """
    Best-effort load of ./config/ds_config.json (when running inside repo).
    When this file is copied to Windows alone, the config likely won't exist.
    """
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        cfg_path = os.path.join(base_dir, "config", "ds_config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_cfg = _load_json_config_defaults()
ETCD_HOST = os.getenv("ETCD_HOST") or (_cfg.get("etcd", {}) or {}).get("host") or "127.0.0.1"
ETCD_PORT = int(os.getenv("ETCD_PORT") or ((_cfg.get("etcd", {}) or {}).get("port") or 2379))
REFRESH_MS = int(os.getenv("REFRESH_MS", "2000"))
MAX_EDGE_LABELS = int(os.getenv("MAX_EDGE_LABELS", "40"))  # avoid clutter
SHOW_EDGE_LABELS = os.getenv("SHOW_EDGE_LABELS", "1") not in ("0", "false", "False")
HEADLESS_FRAMES = int(os.getenv("HEADLESS_FRAMES", "60"))
HEADLESS_OUT_DIR = os.getenv("HEADLESS_OUT_DIR", ".topology_frames")

etcd_client = etcd3.client(host=ETCD_HOST, port=ETCD_PORT)

fig, ax = plt.subplots(figsize=(12, 8))
try:
    fig.canvas.manager.set_window_title("SDN Obfuscation Network - Topology Visualizer")
except Exception:
    pass

global_pos: Dict[str, Tuple[float, float]] = {}


def _fetch_sar_set() -> Set[str]:
    sars: Set[str] = set()
    try:
        for _value, meta in etcd_client.get_prefix("/network/sar/"):
            key = meta.key.decode("utf-8")
            sars.add(key.split("/")[-1])
    except Exception:
        return sars
    return sars


def fetch_graph_from_etcd() -> nx.DiGraph:
    """
    Pull /network/status/ and build a NetworkX DiGraph with attributes.

    Node attrs:
      - kind: "sor" | "sar"
      - cpu, overloaded, city (optional)
    Edge attrs:
      - delay, bw, jitter, loss
    """
    sar_set = _fetch_sar_set()
    g = nx.DiGraph()

    try:
        events = etcd_client.get_prefix("/network/status/")
        for value, meta in events:
            key = meta.key.decode("utf-8")
            node_ip = key.split("/")[-1]
            data = json.loads(value.decode("utf-8"))

            node_status = data.get("node_status", {}) or {}
            cpu_usage = float(node_status.get("cpu_percent", 0.0) or 0.0)
            is_overloaded = bool(node_status.get("is_overloaded", False))
            city = node_status.get("city")
            g.add_node(
                node_ip,
                kind=("sar" if node_ip in sar_set else "sor"),
                cpu=cpu_usage,
                overloaded=is_overloaded,
                city=(str(city) if city is not None and str(city).strip() != "" else None),
            )

            links = data.get("links", {}) or {}
            for neighbor_ip, link_info in links.items():
                if not isinstance(link_info, dict):
                    continue
                status = str(link_info.get("status", "DOWN") or "DOWN")
                if status != "UP":
                    continue

                delay = float(link_info.get("rtt_ms", 9999) or 9999)
                if delay >= 2000:
                    continue
                bw = float(link_info.get("bw", 0.0) or 0.0)
                jitter = float(link_info.get("jitter", 0.0) or 0.0)
                loss = float(link_info.get("loss", 0.0) or 0.0)

                if not g.has_node(neighbor_ip):
                    g.add_node(
                        neighbor_ip,
                        kind=("sar" if neighbor_ip in sar_set else "sor"),
                        cpu=0.0,
                        overloaded=False,
                    )
                g.add_edge(node_ip, neighbor_ip, delay=delay, bw=bw, jitter=jitter, loss=loss)

        # Ensure SAR nodes appear even if they have no status reports.
        for sar_ip in sar_set:
            if not g.has_node(sar_ip):
                g.add_node(sar_ip, kind="sar", cpu=0.0, overloaded=False)

    except Exception as e:
        ax.clear()
        ax.text(0.5, 0.5, f"Failed to fetch etcd data: {e}", ha="center", va="center")
        return nx.DiGraph()

    return g


def animate(_frame: int) -> None:
    global global_pos
    ax.clear()

    g = fetch_graph_from_etcd()
    if g.number_of_nodes() == 0:
        ax.set_title(f"Waiting for nodes... (etcd={ETCD_HOST}:{ETCD_PORT})", fontsize=15, color="gray")
        ax.axis("off")
        return

    # stable spring layout
    # networkx spring_layout expects non-empty 'pos' coordinates if provided.
    if global_pos:
        global_pos = nx.spring_layout(g, pos=global_pos, k=1.2, iterations=25, seed=7)
    else:
        global_pos = nx.spring_layout(g, k=1.2, iterations=25, seed=7)

    # node styling
    node_sizes = []
    node_colors = []
    node_edgecolors = []
    for _n, d in g.nodes(data=True):
        kind = d.get("kind", "sor")
        cpu = float(d.get("cpu", 0.0) or 0.0)
        overloaded = bool(d.get("overloaded", False))

        if kind == "sar":
            node_colors.append("#f4c542")  # gold
            node_edgecolors.append("#2a2a2a")
            node_sizes.append(2400)
        else:
            if overloaded or cpu >= 85.0:
                node_colors.append("#ff4d4d")  # red
            else:
                node_colors.append("#4da6ff")  # blue
            node_edgecolors.append("#1a1a1a")
            node_sizes.append(1400 + min(1600, max(0.0, cpu) * 12))

    nx.draw_networkx_nodes(
        g,
        global_pos,
        ax=ax,
        node_color=node_colors,
        node_size=node_sizes,
        alpha=0.92,
        edgecolors=node_edgecolors,
        linewidths=1.5,
    )
    # Edge styling: color by delay (ms)
    delays = []
    for _u, _v, ed in g.edges(data=True):
        delays.append(float(ed.get("delay", 9999) or 9999))
    if delays:
        vmin = max(1.0, min(delays))
        vmax = max(vmin + 1.0, max(delays))
        norm = mpl.colors.Normalize(vmin=vmin, vmax=vmax)
        cmap = mpl.colormaps.get_cmap("RdYlGn_r")  # low delay green, high delay red
        edge_colors = [cmap(norm(d)) for d in delays]
        widths = [1.4 + min(3.0, max(0.0, (1.0 - (d - vmin) / (vmax - vmin)) * 3.0)) for d in delays]
    else:
        edge_colors = "#666666"
        widths = 1.8

    nx.draw_networkx_edges(
        g,
        global_pos,
        ax=ax,
        width=widths,
        alpha=0.70,
        arrowsize=18,
        edge_color=edge_colors,
        connectionstyle="arc3,rad=0.08",
    )

    # Precompute per-node avg outgoing delay for display.
    avg_out_delay: Dict[str, float] = {}
    for n in g.nodes():
        out_delays = [float(g.edges[n, v].get("delay", 0.0) or 0.0) for v in g.successors(n)]
        if out_delays:
            avg_out_delay[n] = sum(out_delays) / float(len(out_delays))

    labels = {}
    for n, d in g.nodes(data=True):
        kind = d.get("kind", "sor")
        cpu = float(d.get("cpu", 0.0) or 0.0)
        city = d.get("city")
        avg_delay = avg_out_delay.get(n)
        if kind == "sar":
            labels[n] = f"SAR\n{n}"
        else:
            city_line = f"{city}" if city else ""
            delay_line = f"avg {avg_delay:.0f}ms" if avg_delay is not None else ""
            extra = " | ".join([x for x in [city_line, delay_line] if x])
            labels[n] = f"{n}\nCPU {cpu:.0f}%" + (f"\n{extra}" if extra else "")
    nx.draw_networkx_labels(g, global_pos, ax=ax, labels=labels, font_size=9, font_weight="bold", font_color="#111111")

    if SHOW_EDGE_LABELS and g.number_of_edges() <= MAX_EDGE_LABELS:
        edge_labels = {}
        for u, v, ed in g.edges(data=True):
            delay = float(ed.get("delay", 0.0) or 0.0)
            bw = float(ed.get("bw", 0.0) or 0.0)
            jitter = float(ed.get("jitter", 0.0) or 0.0)
            loss = float(ed.get("loss", 0.0) or 0.0)
            edge_labels[(u, v)] = f"{delay:.0f}ms | {bw:.0f}Mb | j{jitter:.0f} | l{loss:.2f}"
        nx.draw_networkx_edge_labels(g, global_pos, ax=ax, edge_labels=edge_labels, font_color="#1f7a1f", font_size=8)

    sors = sum(1 for _n, d in g.nodes(data=True) if d.get("kind") != "sar")
    sars = sum(1 for _n, d in g.nodes(data=True) if d.get("kind") == "sar")
    ax.set_title(
        f"Topology (SOR={sors}, SAR={sars}, edges={g.number_of_edges()}) | etcd={ETCD_HOST}:{ETCD_PORT}",
        fontsize=14,
        fontweight="bold",
        pad=18,
    )
    ax.axis("off")


print(f"[*] Topology visualizer starting (etcd={ETCD_HOST}:{ETCD_PORT}, refresh={REFRESH_MS}ms)")

parser = argparse.ArgumentParser(description="SDN Obfuscation Network topology visualizer (etcd-backed)")
parser.add_argument("--etcd-host", default=ETCD_HOST)
parser.add_argument("--etcd-port", type=int, default=ETCD_PORT)
parser.add_argument("--refresh-ms", type=int, default=REFRESH_MS)
parser.add_argument("--headless", action="store_true", help="Render frames to PNG instead of opening a window")
parser.add_argument("--headless-frames", type=int, default=HEADLESS_FRAMES)
parser.add_argument("--headless-out-dir", default=HEADLESS_OUT_DIR)
args = parser.parse_args()

# #region agent log
_debug_log(
    "H_args",
    "parsed args",
    {
        "etcd_host": args.etcd_host,
        "etcd_port": args.etcd_port,
        "refresh_ms": args.refresh_ms,
        "headless": args.headless,
        "headless_frames": args.headless_frames,
        "headless_out_dir": args.headless_out_dir,
    },
)
# #endregion agent log

ETCD_HOST = args.etcd_host
ETCD_PORT = int(args.etcd_port)
REFRESH_MS = int(args.refresh_ms)
HEADLESS_FRAMES = int(args.headless_frames)
HEADLESS_OUT_DIR = str(args.headless_out_dir)
etcd_client = etcd3.client(host=ETCD_HOST, port=ETCD_PORT)

_is_windows = os.name == "nt"
_has_display = _is_windows or bool(os.getenv("DISPLAY"))

if _has_display and not args.headless:
    # Keep a strong reference in a global name to avoid GC warnings.
    anim = animation.FuncAnimation(fig, animate, interval=REFRESH_MS, cache_frame_data=False)
    plt.tight_layout()
    plt.show()
else:
    # Headless mode: periodically render frames to PNG files.
    os.makedirs(HEADLESS_OUT_DIR, exist_ok=True)
    print(
        f"[*] No DISPLAY detected. Writing {HEADLESS_FRAMES} frames to {HEADLESS_OUT_DIR}/frame_*.png "
        f"(set DISPLAY or use X11 forwarding for an interactive window)."
    )
    for i in range(HEADLESS_FRAMES):
        animate(i)
        plt.tight_layout()
        out = os.path.join(HEADLESS_OUT_DIR, f"frame_{i:04d}.png")
        fig.savefig(out, dpi=140)
        time.sleep(max(0.05, REFRESH_MS / 1000.0))