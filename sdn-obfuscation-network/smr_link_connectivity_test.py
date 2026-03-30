#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SMR -> DS(隧道编排) -> 首跳: 端到端连通性测试 + 选中链路校验

核心能力：
1) 读取 etcd 中 /network/rules/<ip>/tunnel_<id>，从 `first_hop_ip` 还原“选中级联路径”
2) 对路径上的每条链路 u->v 校验：规则是否指向 v、以及 u->v 链路是否在遥测状态中为 UP（可选）
3) 向 `first_hop_ip:first_hop_port` 发送 UDP 探测并等待回包，以验证数据面是否通

用法示例：
  python3 smr_link_connectivity_test.py \
    --first-hop-ip 10.0.0.105 --first-hop-port 22511 --tunnel-id 72614 \
    --probe-mode dns --rounds 3 --timeout-s 2
"""

from __future__ import annotations

import argparse
import json
import random
import socket
import struct
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import etcd3

from common.config import get_settings
from common.etcd_keys import rule_key, status_key


def _parse_json_or_none(raw: Optional[bytes]) -> Optional[Any]:
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return None


def _build_dns_query(qname: str, qtype: int = 1) -> Tuple[int, bytes]:
    """
    构造一个最简 DNS 查询报文（递归期望 RD=1），用于探测“首跳->SAR”是否能收到回包。
    """
    tid = random.randint(0, 0xFFFF)
    # Header: id, flags, qdcount, ancount, nscount, arcount
    # flags=0x0100: RD=1
    header = struct.pack("!HHHHHH", tid, 0x0100, 1, 0, 0, 0)

    # Question: QNAME (labels) + 0 + QTYPE + QCLASS(IN=1)
    parts = [p for p in qname.split(".") if p]
    qname_bytes = b"".join(
        (len(p).to_bytes(1, "big") + p.encode("ascii", errors="ignore")) for p in parts
    ) + b"\x00"
    question = qname_bytes + struct.pack("!HH", qtype, 1)
    return tid, header + question


def _try_parse_dns_response(packet: bytes, expected_tid: int) -> bool:
    if len(packet) < 12:
        return False
    try:
        (tid, flags, qdcount, ancount, nscount, arcount) = struct.unpack("!HHHHHH", packet[:12])
        if tid != expected_tid:
            return False
        qr = (flags >> 15) & 1  # 1 = response
        if qr != 1:
            return False
        # 不强制检查 ancount 等字段，只要“看起来像 response 且 tid 匹配”即可。
        _ = (qdcount, ancount, nscount, arcount)
        return True
    except Exception:
        return False


def reconstruct_path_from_rules(
    *,
    etcd: etcd3.Etcd3Client,
    first_hop_ip: str,
    tunnel_id: int,
    max_steps: int = 32,
) -> Tuple[List[str], Dict[str, dict]]:
    """
    从 etcd 的规则链路中还原选中的级联路径。
    返回：
      - path: [first_hop_ip, ..., sar_ip]
      - rules_by_node: { current_node: rule_json }
    """
    path: List[str] = [first_hop_ip]
    rules_by_node: Dict[str, dict] = {}

    cur = first_hop_ip
    seen = {cur}
    for _ in range(max_steps):
        raw, _ = etcd.get(rule_key(cur, tunnel_id))
        rule = _parse_json_or_none(raw)
        if not rule:
            break
        nxt = rule.get("rip")
        if not nxt:
            break
        nxt = str(nxt)
        rules_by_node[cur] = rule
        path.append(nxt)
        if nxt in seen:
            break
        seen.add(nxt)
        cur = nxt

    return path, rules_by_node


def verify_selected_links(
    *,
    etcd: etcd3.Etcd3Client,
    path: List[str],
    tunnel_id: int,
) -> List[Dict[str, Any]]:
    """
    对路径上的每条边 u->v 做静态校验：
    - etcd 规则是否存在且 rip == v
    - u 的遥测状态中是否把 v 标成 UP（如果没有则给出 unknown）
    """
    results: List[Dict[str, Any]] = []
    for i in range(len(path) - 1):
        u = path[i]
        v = path[i + 1]

        rule_ok = False
        rule = None
        raw_rule, _ = etcd.get(rule_key(u, tunnel_id))
        rule = _parse_json_or_none(raw_rule)
        if isinstance(rule, dict) and str(rule.get("rip")) == v:
            rule_ok = True

        link_state = "unknown"
        raw_status, _ = etcd.get(status_key(u))
        status = _parse_json_or_none(raw_status)
        if isinstance(status, dict):
            links = status.get("links", {}) or {}
            if isinstance(links, dict) and v in links:
                info = links.get(v) or {}
                if isinstance(info, dict):
                    link_state = str(info.get("status", "unknown"))

        results.append(
            {
                "edge": f"{u} -> {v}",
                "rule_rip_match": rule_ok,
                "telemetry_link_status": link_state,
            }
        )
    return results


def run_udp_probe(
    *,
    first_hop_ip: str,
    first_hop_port: int,
    probe_mode: str,
    rounds: int,
    timeout_s: float,
    dns_qname: str,
    raw_message: str,
) -> List[Dict[str, Any]]:
    """
    向首跳发送探测并等待回包（如果目标端口是可响应的服务，例如 DNS）。
    """
    results: List[Dict[str, Any]] = []

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout_s)

    client_tag = uuid.uuid4().hex[:8]
    for i in range(rounds):
        expected_tid: Optional[int] = None
        # 每轮都改一点载荷（raw 模式更直观，dns 模式依赖 tid 已随机）
        if probe_mode == "dns":
            expected_tid, payload = _build_dns_query(dns_qname, qtype=1)
        elif probe_mode == "raw":
            msg = f"{raw_message}#{client_tag}#{i}"
            payload = msg.encode("utf-8", errors="ignore")
        else:
            raise ValueError("probe-mode must be 'dns' or 'raw'")

        start = time.perf_counter()
        sent_len = sock.sendto(payload, (first_hop_ip, int(first_hop_port)))
        ok = False
        rtt_ms: Optional[float] = None
        resp_len: Optional[int] = None
        resp_from: Optional[Tuple[str, int]] = None

        try:
            packet, addr = sock.recvfrom(65535)
            rtt_ms = (time.perf_counter() - start) * 1000.0
            resp_len = len(packet)
            resp_from = (addr[0], addr[1])
            if probe_mode == "dns":
                ok = _try_parse_dns_response(packet, expected_tid=expected_tid or -1)
            else:
                ok = resp_len is not None and resp_len > 0
        except socket.timeout:
            ok = False

        results.append(
            {
                "round": i + 1,
                "sent_bytes": int(sent_len),
                "recv_ok": ok,
                "rtt_ms": rtt_ms,
                "resp_len": resp_len,
                "resp_from": resp_from,
            }
        )

    sock.close()
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Test end-to-end connectivity via selected obfuscated tunnel")
    parser.add_argument("--first-hop-ip", required=True)
    parser.add_argument("--first-hop-port", required=True, type=int)
    parser.add_argument("--tunnel-id", required=True, type=int)
    parser.add_argument("--etcd-host", default=None)
    parser.add_argument("--etcd-port", default=None, type=int)
    parser.add_argument("--probe-mode", default="dns", choices=["dns", "raw"])
    parser.add_argument("--rounds", default=3, type=int)
    parser.add_argument("--timeout-s", default=2.0, type=float)
    parser.add_argument("--dns-qname", default="example.com")
    parser.add_argument("--raw-message", default="HELLO-SDN-OBFUSCATION-TEST")
    parser.add_argument("--max-steps", default=32, type=int)
    args = parser.parse_args()

    settings = get_settings()
    etcd_host = args.etcd_host or settings.etcd_host
    etcd_port = args.etcd_port or settings.etcd_port
    etcd = etcd3.client(host=etcd_host, port=etcd_port)

    print("=====================================================")
    print("[1/2] 选中链路/路径校验（从 etcd rules 还原）")
    path, rules_by_node = reconstruct_path_from_rules(
        etcd=etcd,
        first_hop_ip=args.first_hop_ip,
        tunnel_id=args.tunnel_id,
        max_steps=args.max_steps,
    )

    print(f"Selected path: {' -> '.join(path)}")
    print(f"Rules loaded: {len(rules_by_node)}  (tunnel_id={args.tunnel_id})")

    links = verify_selected_links(etcd=etcd, path=path, tunnel_id=args.tunnel_id)
    rule_ok_cnt = sum(1 for x in links if x["rule_rip_match"])
    print(f"Edge rule verification: {rule_ok_cnt}/{len(links)} edges rip-match")
    for x in links:
        edge = x["edge"]
        rule_ok = x["rule_rip_match"]
        tele = x["telemetry_link_status"]
        print(f"  - {edge} | rule_rip_match={rule_ok} | telemetry_status={tele}")

    print("-----------------------------------------------------")
    print("[2/2] 数据面连通性探测（向首跳 UDP 发包并等回包）")
    print(f"Probe target: {args.first_hop_ip}:{args.first_hop_port}  mode={args.probe_mode}  rounds={args.rounds}")

    probe_results = run_udp_probe(
        first_hop_ip=args.first_hop_ip,
        first_hop_port=args.first_hop_port,
        probe_mode=args.probe_mode,
        rounds=args.rounds,
        timeout_s=args.timeout_s,
        dns_qname=args.dns_qname,
        raw_message=args.raw_message,
    )

    success = sum(1 for r in probe_results if r["recv_ok"])
    print(f"UDP probe success: {success}/{len(probe_results)}")
    for r in probe_results:
        rt = r["rtt_ms"]
        rt_s = f"{rt:.2f}ms" if isinstance(rt, (int, float)) else "-"
        print(
            f"  round={r['round']} recv_ok={r['recv_ok']} sent={r['sent_bytes']} resp_len={r['resp_len']} rtt={rt_s} resp_from={r['resp_from']}"
        )


if __name__ == "__main__":
    main()

