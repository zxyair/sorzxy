import etcd3
import json
import time
import random
import threading
import math
#模拟验证邻居分配算法
# ==========================================
# 1. 初始化设置
# ==========================================
ETCD_HOST = '127.0.0.1' # 如果你在本地跑，记得改成云服务器公网 IP
etcd_client = etcd3.client(host=ETCD_HOST, port=2379)

# 定义要伪造的节点数量
NUM_NODES = 10
MOCK_IPS = [f"10.0.0.{101+i}" for i in range(NUM_NODES)]

# 中国及国际城市坐标（纬度, 经度）
CITY_COORDINATES = {
    "北京": (39.9042, 116.4074),
    "上海": (31.2304, 121.4737),
    "广州": (23.1291, 113.2644),
    "深圳": (22.5431, 114.0579),
    "成都": (30.5728, 104.0668),
    "武汉": (30.5928, 114.3055),
    "西安": (34.3416, 108.9398),
    "南京": (32.0603, 118.7969),
    "杭州": (30.2741, 120.1551),
    "天津": (39.3434, 117.3616),
    "重庆": (29.5630, 106.5516),
    "沈阳": (41.8057, 123.4315),
    "青岛": (36.0671, 120.3826),
    "厦门": (24.4798, 118.0894),
    "哈尔滨": (45.8038, 126.5349),
    "香港": (22.3193, 114.1694),
    "新加坡": (1.3521, 103.8198),
    "硅谷": (37.3387, -121.8853),
    "法兰克福": (50.1109, 8.6821),
}

def haversine_distance(lat1, lon1, lat2, lon2):
    """计算两个经纬度坐标之间的距离（公里）"""
    R = 6371.0  # 地球半径，公里
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlon = lon2_rad - lon1_rad
    dlat = lat2_rad - lat1_rad
    a = math.sin(dlat / 2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    distance = R * c
    return distance

# 提前设定每个 IP 对应的城市（固定映射）
IP_TO_CITY = {
    # "10.0.0.100": "北京", # 预留给 SAR 业务节点
    "10.0.0.101": "上海",
    "10.0.0.102": "广州",
    "10.0.0.103": "深圳",
    "10.0.0.104": "成都",
    "10.0.0.105": "武汉",
    "10.0.0.106": "香港",
    "10.0.0.107": "新加坡",
    "10.0.0.108": "硅谷",
    "10.0.0.109": "法兰克福",
    "10.0.0.110": "西安",
}

def mock_agent_lifecycle(my_ip):
    """
    模拟一个独立 Agent 的完整生命周期：
    注册上线 -> 读取 DS 盲探指令 -> 伪造测距 RTT -> 持续上报状态
    """
    my_neighbors = []
    my_id = int(my_ip.split('.')[-1])

    # 1. 初始上线 (触发 DS 第一阶段)
    status_data = {
        "node_status": {"cpu_percent": random.randint(10, 30), "is_overloaded": False},
        "links": {}
    }
    etcd_client.put(f"/network/status/{my_ip}", json.dumps(status_data))

    while True:
        try:
            # 2. 倾听 DS 大脑的指令
            config_val, _ = etcd_client.get(f"/network/config/{my_ip}/neighbors")
            if config_val:
                my_neighbors = json.loads(config_val.decode('utf-8'))

            links = {}
            # 3. 伪造真实的物理环境 (神来之笔) - 基于地理距离的延迟模型
            my_city = IP_TO_CITY.get(my_ip)
            if my_city is None:
                # 如果 IP 未在映射中，回退到基于 ID 的简单模型
                my_city = "北京"
            my_lat, my_lon = CITY_COORDINATES[my_city]
            
            for neighbor in my_neighbors:
                neighbor_city = IP_TO_CITY.get(neighbor)
                if neighbor_city is None:
                    neighbor_city = "北京"
                nb_lat, nb_lon = CITY_COORDINATES[neighbor_city]
                
                # 计算地理距离（公里）
                distance_km = haversine_distance(my_lat, my_lon, nb_lat, nb_lon)
                
                # 基于距离的 RTT 计算：每公里 0.02 ms + 随机扰动 2~5 ms
                # 同一城市距离为0，设置基础 RTT 为 1~3 ms
                if distance_km < 1.0:
                    base_rtt = random.uniform(1.0, 3.0)
                else:
                    base_rtt = distance_km * 0.02 + random.uniform(2.0, 5.0)
                
                links[neighbor] = {
                    "rtt_ms": round(base_rtt, 2),
                    "status": "UP",
                    # 🌟 修复关键：给每条内部链路注入随机的充沛带宽 (如 200~800 Mbps)
                    "bw": random.uniform(200.0, 800.0),
                    # V1+: inject jitter/loss to validate DS multi-QoS pipeline
                    "jitter": random.uniform(1.0, 15.0),
                    "loss": random.uniform(0.0, 0.02),
                }

            # 4. 上报包含 RTT 矩阵的最新状态 (触发 DS 第二阶段)
            status_data["node_status"]["cpu_percent"] = random.randint(10, 45)
            status_data["links"] = links
            etcd_client.put(f"/network/status/{my_ip}", json.dumps(status_data))

        except Exception as e:
            pass

        time.sleep(3) # 模拟 3 秒心跳周期

if __name__ == "__main__":
    print("=====================================================")
    print("🎭 入度均衡测试引擎：大规模幻影集群启动")
    print("=====================================================")
    
    # 清理 etcd 旧数据，保证每次测试都是纯净的
    etcd_client.delete_prefix("/network/")
    print("[*] 已清理 etcd 历史网络数据，准备注入新节点...\n")
    
    threads = []
    # 每隔 1 秒启动一个节点，模拟真实世界中节点依次上线的动态过程
    for ip in MOCK_IPS:
        print(f"[*] 🚀 部署虚拟节点 {ip} ...")
        t = threading.Thread(target=mock_agent_lifecycle, args=(ip,), daemon=True)
        t.start()
        threads.append(t)
        time.sleep(1.5) 

    print("\n[+] 10 个虚拟节点已全部切入网络，正在与 DS 交互...")
    print("[+] 请盯着 topology_visualizer 大屏上的拓扑生长过程！\n")
    
    # 保持主线程存活
    while True:
        time.sleep(10)