### SOR 混淆网络在腾讯云上的快速部署指南

本指南假设腾讯云主机上已经安装好 `python`/`python3`，目标是在最少依赖的前提下拉起 etcd、DS、若干 SOR Agent，并通过 `smr_client.py` 做端到端验证。

#### 1. 安装依赖

1. 克隆或复制本仓库到腾讯云主机，例如：

```bash
git clone <your-repo-url> sdn-obfuscation-network
cd sdn-obfuscation-network
```

2. 安装 Python 依赖：

```bash
pip3 install -r requirements.txt
```

3. 安装 etcd（任选其一）：
   - 使用系统包管理器安装；
   - 或从官方发布页下载二进制，放在 PATH 中。

#### 2. 配置集中配置文件或环境变量

1. 确认 `config/ds_config.json` 中的 etcd 地址、DS 监听地址等符合当前环境，尤其是：
   - `"etcd.host"`：etcd 的服务 IP（腾讯云内网 IP 或 `127.0.0.1`）；
   - `"ds.grpc_bind"`：DS 在服务器上的监听地址（例如 `[::]:50051`）；
   - `"ds.grpc_target"`：SMR 访问 DS 使用的地址（例如 `10.0.0.5:50051`）。

2. 可选地，用环境变量覆盖部分字段，例如：

```bash
export ETCD_HOST=10.0.0.5
export ETCD_PORT=2379
export MAX_VOICE_DELAY_MS=150
```

#### 3. 启动 etcd

在计划作为控制平面的主机上运行：

```bash
etcd --advertise-client-urls http://<ETCD_IP>:2379 \
     --listen-client-urls http://0.0.0.0:2379
```

确保 `config/ds_config.json` 或环境变量中的 `ETCD_HOST`/`ETCD_PORT` 与之匹配。

#### 4. 启动 DS 目录服务器

在项目根目录执行：

```bash
python3 ds_server.py
```

若日志中出现“拓扑大脑初始化完毕”“目录服务器已启动”等字样，说明 DS 与 etcd 通信正常。

#### 5. 启动若干 SOR Agent

在每一台作为 SOR 节点的腾讯云主机上（可以与 DS 共机，也可以独立）：

1. 将本项目代码复制到该主机，并安装依赖。
2. 确保该主机可以访问 etcd 地址（网络连通）。
3. 在项目根目录执行：

```bash
python3 agent.py
```

Agent 启动后将：
- 定期上报自身状态到 `/network/status/{MY_IP}`；
- 监听 DS 下发的邻居配置和流表规则；
- 对邻居执行 ping 探测并上报链路 QoS。

#### 6. 启动 SAR 并完成注册

系统中只有一个 SAR 时，确保业务目标侧有逻辑向 etcd 注册，例如写入：

```text
/network/sar/<SAR_IP>
```

DS 会根据该信息感知目标，并在算路时将路径终点指向该 SAR。

#### 7. 运行 SMR 客户端进行接入测试

在任意一台可以访问 DS 的主机上，运行：

```bash
python3 smr_client.py --smr-id SMR-Client-001 --bandwidth 100
```

若成功，终端将打印：
- 分配的 `Tunnel ID`；
- 首跳接入点 `first_hop_ip:first_hop_port`；
- DS 的提示消息。

此时，将实际语音/业务流量打向该首跳，即可经过 SOR 隐匿级联转发至 SAR。

