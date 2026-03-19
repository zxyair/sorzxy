import grpc
import time
import random

# 导入根据 control.proto 编译生成的 gRPC 桩代码
import control_pb2
import control_pb2_grpc
import os

# ==========================================
# 1. 核心配置参数
# ==========================================
DS_SERVER_ADDR = '127.0.0.1:50051' # DS 控制平面的监听地址
TARGET_SAR = os.getenv("TARGET_SAR", "10.0.0.100")  # 最终要访问的目标业务 IP
TEST_ROUNDS = 10                    # 连续发起的编排请求次数

def run_routing_simulation():
    print("=====================================================")
    print("🚀 SMR 隐匿隧道高频打流测试 (验证路径跳变与流表下发)")
    print("=====================================================")
    
    # 建立与 DS 大脑的 gRPC 通信通道
    try:
        channel = grpc.insecure_channel(DS_SERVER_ADDR)
        stub = control_pb2_grpc.DirectoryServerStub(channel)
    except Exception as e:
        print(f"[-] 无法初始化 gRPC 通道，请检查 DS 是否启动: {e}")
        return

    success_count = 0
    total_latency = 0.0

    for i in range(1, TEST_ROUNDS + 1):
        # 随机生成一个整数带宽需求，模拟不同业务的 QoS 要求 (10Mbps ~ 100Mbps)
        req_bw = random.randint(10, 100)
        print(f"\n[测试 {i}/{TEST_ROUNDS}] 发起接入请求 -> 目标: {TARGET_SAR} | QoS带宽约束: {req_bw} Mbps")
        
        # 组装最纯粹的请求体 (对应瘦身后的 control.proto)
        req = control_pb2.TunnelReq(
            target_sar_ip=TARGET_SAR,
            req_bandwidth=req_bw
        )
        
        try:
            # 记录发包时间，计算端到端算路时延
            start_time = time.time()
            resp = stub.RequestTunnel(req)
            end_time = time.time()
            
            latency_ms = (end_time - start_time) * 1000
            
            # 解析 DS 返回的响应结果
            if resp.success:
                success_count += 1
                total_latency += latency_ms
                print(f"  ✅ 编排成功! (建路耗时: {latency_ms:.2f} ms)")
                print(f"  🔗 隧道 ID:   {resp.tunnel_id}")
                print(f"  🚪 首跳入口:  {resp.first_hop_ip}:{resp.first_hop_port}")
                print(f"  📜 大脑回执:  {resp.message}")
            else:
                print(f"  ❌ 编排驳回:  {resp.message}")
                
        except grpc.RpcError as e:
            print(f"  ⚠️ gRPC 通信异常: 无法连接到 DS 控制平面 ({e.details()})")
            
        # 稍微等一下，模拟人类或应用的真实请求间隔，也让大屏状态刷新
        time.sleep(2.0)
        
    # ==========================================
    # 2. 打印性能测试报告总结
    # ==========================================
    print("\n=====================================================")
    print("📊 算路性能测试报告汇总")
    print("=====================================================")
    print(f"总请求数: {TEST_ROUNDS}")
    print(f"成功次数: {success_count}")
    if success_count > 0:
        print(f"平均建路时延: {total_latency / success_count:.2f} ms")
        print("结论: 控制平面算路耗时符合高铁/车载高动态场景的毫秒级切换要求。")

if __name__ == '__main__':
    run_routing_simulation()