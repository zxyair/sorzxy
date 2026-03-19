# SDN Obfuscation Network (V1) – Minimal Runbook

本 Runbook 面向**第一版单机 DS + 多 SOR +（可选）SAR +（可选）SMR**的最小端到端跑通。

## 0. 目录与关键组件

- **项目根目录**：`/home/sorzxy/sdn-obfuscation-network/`
- **SOR Agent**：`agent.py`
- **目录服务器 DS**：`ds_server.py`（gRPC 监听 `:50051`）
- **协议**：`control.proto` / `control_pb2*.py`
- **数据面转发执行器**：`tinymapper`
  - 项目根目录有一份：`/home/sorzxy/sdn-obfuscation-network/tinymapper`
  - 也可用全局 bin 中的：`/home/sorzxy/bin/tinymapper`
  - `./tinymapper -h` 显示其为 tinyPortMapper，支持 `-l/-r` 与 `-u/-t`。

## 1. Python 依赖与虚拟环境

系统 Python (`/usr/bin/python3`) **不包含** `etcd3` 等依赖；依赖安装在项目自带 venv。

在每个节点/终端执行：

```bash
cd /home/sorzxy/sdn-obfuscation-network
source venv/bin/activate
python -c "import etcd3,grpc,networkx; print('deps-ok')"
```

## 2. etcd（控制信道/状态中心）

### 2.1 检查 etcd 是否已运行

```bash
ps aux | grep -E "[e]tcd"
```

当前环境中通常已存在 root 启动的 etcd（示例参数）：

- `--listen-client-urls=http://0.0.0.0:2379`
- `--advertise-client-urls=http://<公网IP>:2379`

### 2.2 关键前缀

- **SOR 遥测上报**：`/network/status/<sor_ip>`
- **DS 下发邻居探测任务**：`/network/config/<sor_ip>/neighbors`
- **DS 下发流表/隧道规则**：`/network/rules/<sor_ip>/tunnel_<id>`
- **SAR 注册**：`/network/sar/<sar_ip>`

### 2.3 清理历史数据（强烈建议每次测试前）

```bash
etcdctl --endpoints=http://127.0.0.1:2379 del --prefix /network/
```

也可以只清理流表或遥测：

```bash
etcdctl --endpoints=http://127.0.0.1:2379 del --prefix /network/rules/
etcdctl --endpoints=http://127.0.0.1:2379 del --prefix /network/status/
```

### 2.4 观测 key（调试用）

```bash
etcdctl --endpoints=http://127.0.0.1:2379 get --prefix /network/status/
etcdctl --endpoints=http://127.0.0.1:2379 get --prefix /network/config/
etcdctl --endpoints=http://127.0.0.1:2379 get --prefix /network/rules/
```

## 3. 最小启动顺序（本机单机版）

在 4 个终端分别运行。

### 3.1 启动 DS（目录服务器）

```bash
cd /home/sorzxy/sdn-obfuscation-network
source venv/bin/activate
python ds_server.py
```

预期：打印 DS 启动日志，并开始 watch `/network/status/`。

### 3.2 启动 1..N 个 SOR（混淆传输节点）

在每个 SOR 节点上：

```bash
cd /home/sorzxy/sdn-obfuscation-network
source venv/bin/activate
python agent.py
```

预期：

- SOR 周期性写入 `/network/status/<ip>`
- SOR watch `/network/config/<ip>/neighbors` 接收 DS 的邻居探测任务
- SOR watch `/network/rules/<ip>/` 接收 DS 的隧道规则，并拉起 `tinymapper` 子进程

### 3.3 （可选）启动 SAR 注册

```bash
cd /home/sorzxy/sdn-obfuscation-network
source venv/bin/activate
python sar_register.py
```

默认会注册：

- `SAR_IP=10.0.0.100`
- `SERVICE_PORT=8088`

### 3.4 （可选）启动 SMR 请求隧道

方式 A：单次请求（`smr_client.py` 默认 target 为 `8.8.8.8`，用于 demo）

```bash
cd /home/sorzxy/sdn-obfuscation-network
source venv/bin/activate
python smr_client.py
```

方式 B：高频请求压测（推荐）

```bash
cd /home/sorzxy/sdn-obfuscation-network
source venv/bin/activate
python mock_smr_client_test_cmo_pdfs.py
```

预期：DS 打印“算路成功 + 下发流表”，SOR 打印“收到 DS 新规则 + 拉起 tinymapper PID”。

### 3.5 （可选）对首跳发包验证数据面

把 DS 返回的 `first_hop_ip:first_hop_port` 填入：

```bash
cd /home/sorzxy/sdn-obfuscation-network
source venv/bin/activate
python smr_probe.py <首跳IP> <首跳PORT>
```

## 4. 拓扑与邻居指派验证（不依赖真实 SOR）

使用模拟脚本自动注入 10 个虚拟节点并触发邻居指派状态机：

```bash
cd /home/sorzxy/sdn-obfuscation-network
source venv/bin/activate
python mock_cluster_neighbor_assignment.py
```

（可选）打开拓扑可视化：

> 注意：`topology_visualizer.py` 内的 `ETCD_HOST` 目前写死为公网 IP，需要按实际环境修改后再运行。

```bash
cd /home/sorzxy/sdn-obfuscation-network
source venv/bin/activate
python topology_visualizer.py
```

## 5. 常见问题

- **`ModuleNotFoundError: etcd3`**：没激活 venv，请执行 `source venv/bin/activate`。
- **SOR 拉不起 `tinymapper`**：确认 `tinymapper` 可执行权限；并确认当前工作目录是项目根（因为 `agent.py` 使用 `./tinymapper`）。
- **etcd 数据目录权限问题**：当前环境存在 root-owned 的 `default.etcd`；第一版建议统一用系统已运行的 etcd（或为非 root 另起 data-dir）。

