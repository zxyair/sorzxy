#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

import etcd3
import grpc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import control_pb2
import control_pb2_grpc
from common.config import get_settings
from common.etcd_keys import rule_key, status_key


def _parse_json_or_none(raw: bytes | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def reconstruct_path(etcd: etcd3.Etcd3Client, first_hop_ip: str, tunnel_id: int, max_steps: int = 32) -> Tuple[List[str], Dict[str, dict]]:
    path: List[str] = [first_hop_ip]
    rules_by_node: Dict[str, dict] = {}
    cur = first_hop_ip
    seen = {cur}
    for _ in range(max_steps):
        raw, _ = etcd.get(rule_key(cur, tunnel_id))
        rule = _parse_json_or_none(raw)
        if not isinstance(rule, dict):
            break
        nxt = str(rule.get("rip", ""))
        if not nxt:
            break
        rules_by_node[cur] = rule
        path.append(nxt)
        if nxt in seen:
            break
        seen.add(nxt)
        cur = nxt
    return path, rules_by_node


def _edge_metrics(etcd: etcd3.Etcd3Client, u: str, v: str) -> Dict[str, Any]:
    raw, _ = etcd.get(status_key(u))
    status = _parse_json_or_none(raw)
    if not isinstance(status, dict):
        return {"status": "unknown", "delay_ms": None, "jitter_ms": None, "loss_rate": None, "bw_mbps": None}
    links = status.get("links", {}) or {}
    info = links.get(v) if isinstance(links, dict) else None
    if not isinstance(info, dict):
        return {"status": "unknown", "delay_ms": None, "jitter_ms": None, "loss_rate": None, "bw_mbps": None}
    return {
        "status": str(info.get("status", "unknown")),
        "delay_ms": float(info["rtt_ms"]) if "rtt_ms" in info else None,
        "jitter_ms": float(info["jitter"]) if "jitter" in info else None,
        "loss_rate": float(info["loss"]) if "loss" in info else None,
        "bw_mbps": float(info["bw"]) if "bw" in info else None,
    }


def aggregate_qos(etcd: etcd3.Etcd3Client, path: List[str]) -> Dict[str, Any]:
    edges = []
    delays: List[float] = []
    jitters: List[float] = []
    losses: List[float] = []
    bws: List[float] = []
    for i in range(len(path) - 1):
        u = path[i]
        v = path[i + 1]
        m = _edge_metrics(etcd, u, v)
        edges.append({"from": u, "to": v, **m})
        if m["delay_ms"] is not None:
            delays.append(float(m["delay_ms"]))
        if m["jitter_ms"] is not None:
            jitters.append(float(m["jitter_ms"]))
        if m["loss_rate"] is not None:
            losses.append(float(m["loss_rate"]))
        if m["bw_mbps"] is not None:
            bws.append(float(m["bw_mbps"]))
    return {
        "edges": edges,
        "path_delay_ms": sum(delays) if delays else None,
        "path_jitter_ms": max(jitters) if jitters else None,
        "path_loss_rate": (1.0 - math.prod([(1.0 - x) for x in losses])) if losses else None,
        "path_min_bw_mbps": min(bws) if bws else None,
    }


def compliance_judgement(qos: Dict[str, Any], hop_count: int, req_bw: float, cfg: Dict[str, Any]) -> Dict[str, Any]:
    checks = {}
    checks["delay"] = qos["path_delay_ms"] is not None and qos["path_delay_ms"] <= float(cfg["max_voice_delay_ms"])
    checks["jitter"] = qos["path_jitter_ms"] is not None and qos["path_jitter_ms"] <= float(cfg["max_jitter_ms"])
    checks["loss"] = qos["path_loss_rate"] is not None and qos["path_loss_rate"] <= float(cfg["max_loss_rate"])
    bw_need = max(float(cfg["min_bw_mbps"]), float(req_bw))
    checks["bw"] = qos["path_min_bw_mbps"] is not None and qos["path_min_bw_mbps"] >= bw_need
    checks["min_hops"] = hop_count >= int(cfg["min_hops"])
    checks["max_hops"] = hop_count <= int(cfg["max_hops"])
    checks["all"] = all(checks.values())
    return checks


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect path quality/diversity evidence")
    parser.add_argument("--rounds", type=int, default=400)
    parser.add_argument("--concurrency", type=int, default=24)
    parser.add_argument("--out-jsonl", default=".paper/path_quality_diversity_results.jsonl")
    parser.add_argument("--out-summary", default=".paper/path_quality_diversity_summary.json")
    args = parser.parse_args()

    settings = get_settings()
    qos_cfg = {
        "max_voice_delay_ms": settings.max_voice_delay_ms,
        "max_jitter_ms": settings.max_jitter_ms,
        "max_loss_rate": settings.max_loss_rate,
        "min_bw_mbps": settings.min_bw_mbps,
        "min_hops": settings.min_hops,
        "max_hops": settings.max_hops,
    }

    def one_request(i: int) -> Dict[str, Any]:
        req_bw = random.randint(10, 100)
        t0 = time.time()
        row: Dict[str, Any] = {"idx": i, "req_bw_mbps": req_bw}
        try:
            ch = grpc.insecure_channel(settings.ds_grpc_target)
            stub = control_pb2_grpc.DirectoryServerStub(ch)
            req = control_pb2.TunnelReq(target_sar_ip="auto", req_bandwidth=req_bw)
            resp = stub.RequestTunnel(req, timeout=10)
            row["ok"] = bool(resp.success)
            row["lat_ms"] = round((time.time() - t0) * 1000.0, 2)
            row["message"] = str(resp.message)
            if not resp.success:
                return row
            row["tunnel_id"] = int(resp.tunnel_id)
            row["first_hop_ip"] = str(resp.first_hop_ip)
            row["first_hop_port"] = int(resp.first_hop_port)
        except Exception as e:
            row["ok"] = False
            row["lat_ms"] = None
            row["message"] = f"ERR:{type(e).__name__}:{e}"
            return row

        # Separate etcd client in thread for safety.
        etcd = etcd3.client(host=settings.etcd_host, port=settings.etcd_port)
        path, rules = reconstruct_path(etcd, row["first_hop_ip"], row["tunnel_id"])
        row["selected_path"] = path
        row["path_edges"] = [f"{path[i]}->{path[i+1]}" for i in range(len(path) - 1)]
        row["hop_count"] = max(0, len(path) - 2)
        row["path_reconstructed"] = len(path) >= 2 and len(rules) >= 1
        qos = aggregate_qos(etcd, path)
        row["qos_observed"] = {
            "path_delay_ms": qos["path_delay_ms"],
            "path_jitter_ms": qos["path_jitter_ms"],
            "path_loss_rate": qos["path_loss_rate"],
            "path_min_bw_mbps": qos["path_min_bw_mbps"],
        }
        row["qos_checks"] = compliance_judgement(row["qos_observed"], row["hop_count"], req_bw, qos_cfg)
        return row

    rows: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futures = [ex.submit(one_request, i) for i in range(1, args.rounds + 1)]
        for f in as_completed(futures):
            rows.append(f.result())
    rows.sort(key=lambda x: x["idx"])

    out_jsonl = Path(args.out_jsonl)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    out_jsonl.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    ok_rows = [r for r in rows if r.get("ok")]
    reconstructed = [r for r in ok_rows if r.get("path_reconstructed")]
    compliant = [r for r in reconstructed if r.get("qos_checks", {}).get("all")]

    # diversity metrics
    paths = [r["selected_path"] for r in reconstructed if isinstance(r.get("selected_path"), list)]
    edge_sets = [set(r["path_edges"]) for r in reconstructed]
    jaccard_vals: List[float] = []
    edit_norm_vals: List[float] = []
    for i in range(len(paths)):
        for j in range(i + 1, len(paths)):
            a, b = edge_sets[i], edge_sets[j]
            if len(a | b) == 0:
                continue
            jaccard_vals.append(len(a & b) / len(a | b))
            d = levenshtein(paths[i], paths[j])
            denom = max(len(paths[i]), len(paths[j]), 1)
            edit_norm_vals.append(d / denom)

    path_sig = ["->".join(p) for p in paths]
    path_counter = Counter(path_sig)
    unique_ratio = (len(path_counter) / len(paths)) if paths else 0.0
    probs = [c / len(paths) for c in path_counter.values()] if paths else []
    entropy = -sum(p * math.log2(p) for p in probs) if probs else 0.0

    dim_rates = {}
    for k in ["delay", "jitter", "loss", "bw", "min_hops", "max_hops"]:
        vals = [bool(r.get("qos_checks", {}).get(k, False)) for r in reconstructed]
        dim_rates[k] = (sum(vals) / len(vals)) if vals else 0.0

    summary = {
        "dataset": {
            "rounds": args.rounds,
            "concurrency": args.concurrency,
            "success_count": len(ok_rows),
            "success_rate": (len(ok_rows) / len(rows)) if rows else 0.0,
            "path_reconstructed_count": len(reconstructed),
            "path_reconstructed_rate": (len(reconstructed) / len(ok_rows)) if ok_rows else 0.0,
        },
        "qos_constraints": qos_cfg,
        "qos_compliance": {
            "overall_compliance_count": len(compliant),
            "overall_compliance_rate": (len(compliant) / len(reconstructed)) if reconstructed else 0.0,
            "dimension_pass_rate": dim_rates,
        },
        "diversity": {
            "path_unique_count": len(path_counter),
            "path_unique_ratio": unique_ratio,
            "path_entropy_bits": entropy,
            "top_paths": path_counter.most_common(10),
            "pairwise_jaccard": {
                "count": len(jaccard_vals),
                "mean": statistics.mean(jaccard_vals) if jaccard_vals else None,
                "median": statistics.median(jaccard_vals) if jaccard_vals else None,
                "p90": sorted(jaccard_vals)[int(0.9 * len(jaccard_vals)) - 1] if len(jaccard_vals) >= 10 else (max(jaccard_vals) if jaccard_vals else None),
            },
            "pairwise_norm_edit_distance": {
                "count": len(edit_norm_vals),
                "mean": statistics.mean(edit_norm_vals) if edit_norm_vals else None,
                "median": statistics.median(edit_norm_vals) if edit_norm_vals else None,
                "p90": sorted(edit_norm_vals)[int(0.9 * len(edit_norm_vals)) - 1] if len(edit_norm_vals) >= 10 else (max(edit_norm_vals) if edit_norm_vals else None),
            },
        },
    }

    out_summary = Path(args.out_summary)
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"wrote: {out_jsonl}")
    print(f"wrote: {out_summary}")


if __name__ == "__main__":
    main()
