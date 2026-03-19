import etcd3
import json
import os
import time

# ==========================================
# 核心配置参数
# ==========================================
ETCD_HOST = '127.0.0.1'
SAR_IP = os.getenv("SAR_IP", "10.0.0.100")  # 真实的业务目标 IP
SERVICE_PORT = int(os.getenv("SERVICE_PORT", "8088"))  # 业务端口 (如 VoIP 信令或 DNS)

def register_sar_lifecycle():
    print("=====================================================")
    print(f"🎯 SAR 业务容灾节点启动注册引擎 | IP: {SAR_IP}")
    print("=====================================================")
    
    try:
        etcd_client = etcd3.client(host=ETCD_HOST, port=2379)
    except Exception as e:
        print(f"[-] 无法连接到 etcd: {e}")
        return

    # SAR 专属的注册目录
    registry_key = f"/network/sar/{SAR_IP}"
    
    # 业务元数据
    sar_meta = {
        "service_type": "voip_dr", # 语音容灾业务
        "port": SERVICE_PORT,
        "status": "UP",
        "register_time": time.time()
    }

    try:
        while True:
            # 持续发送心跳，宣告自己活着
            etcd_client.put(registry_key, json.dumps(sar_meta))
            print(f"[*] 💓 SAR [{SAR_IP}] 存活心跳已广播至全网 -> etcd ({registry_key})")
            time.sleep(5) # 5秒一次心跳
            
    except KeyboardInterrupt:
        print(f"\n[!] SAR [{SAR_IP}] 节点下线，清理注册信息...")
        etcd_client.delete(registry_key)

if __name__ == "__main__":
    register_sar_lifecycle()