import argparse
import json

import grpc

import control_pb2
import control_pb2_grpc
from common.config import get_settings


def request_tunnel(smr_id: str, req_bandwidth: float) -> control_pb2.TunnelResp:
    """
    向目录服务器 DS 发送隧道接入请求，并返回原始响应对象。
    SAR 具体 IP 由 DS 内部根据当前唯一 SAR 或配置确定，这里仅填占位符。
    """
    settings = get_settings()
    channel = grpc.insecure_channel(settings.ds_grpc_target)
    stub = control_pb2_grpc.DirectoryServerStub(channel)

    # target_sar_ip 目前对客户端来说是逻辑占位，真实目标由 DS 自行决策
    request = control_pb2.TunnelReq(
        smr_id=smr_id,
        target_sar_ip="default_sar",
        req_bandwidth=req_bandwidth,
    )

    return stub.RequestTunnel(request)


def main() -> None:
    parser = argparse.ArgumentParser(description="SMR client for requesting obfuscated tunnel from DS")
    parser.add_argument(
        "--smr-id",
        default="SMR-Client-001",
        help="Logical identifier of this SMR endpoint",
    )
    parser.add_argument(
        "--bandwidth",
        type=float,
        default=100.0,
        help="Requested end-to-end bandwidth in Mbps",
    )
    args = parser.parse_args()

    print("[*] SMR 终端启动，正在向 DS 目录服务器请求隐匿隧道...")

    try:
        response = request_tunnel(smr_id=args.smr_id, req_bandwidth=args.bandwidth)
    except grpc.RpcError as e:
        print(f"\n[-] 请求失败，DS 未响应: {e.details()}")
        return

    if not getattr(response, "success", True):
        # message 可能是 JSON，原样输出以便排查 QoS 约束失败原因
        msg = getattr(response, "message", "")
        try:
            parsed = json.loads(msg)
            pretty = json.dumps(parsed, ensure_ascii=False, indent=2)
            print("\n[-] 隧道编排失败，DS 返回：")
            print(pretty)
        except Exception:
            print("\n[-] 隧道编排失败，DS 返回：")
            print(msg)
        return

    print("\n[+] 隧道编排成功！收到 DS 下发的接入凭证：")
    print(f"    分配的 Tunnel ID: {response.tunnel_id}")
    print(f"    首跳接入点: {response.first_hop_ip}:{response.first_hop_port}")
    print(f"    DS 留言: {response.message}")


if __name__ == "__main__":
    main()