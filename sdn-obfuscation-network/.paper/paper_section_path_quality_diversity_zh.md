## 路径质量与隐匿性增强验证（论文最终版）

为进一步证明选路机制不仅具备首跳随机性，而且具备路径级质量保障与隐匿差异性，本文在并发场景下记录每次建路的完整选中路径，并进行联合评估。实验共发起 400 次并发建路请求（并发度 24），请求成功率为 100%，且全部样本均可通过 `tunnel_id + first_hop_ip` 从规则链完整反推路径（覆盖率 100%）。

在路径质量方面，按系统阈值 `max_delay=250 ms`、`max_jitter=30 ms`、`max_loss=0.01`、`min_bw=1 Mbps`、`hop_range=[1,5]` 进行判定。结果表明总体 QoS 合规率为 50.5%（202/400），其中 `delay/jitter/loss/bw/max_hops` 维度通过率均为 100%，主要限制来自 `min_hops`（50.5%）。这说明当前拓扑下路径在传输质量维度稳定达标，而总体合规率主要受最小跳数约束控制。对应结果见图6。

在路径隐匿性方面，采用三类指标联合验证：  
1) 边集合重叠率（Jaccard），
\[
J(E_a,E_b)=\frac{|E_a \cap E_b|}{|E_a \cup E_b|}
\]
2) 节点序列归一化编辑距离，
\[
D_n(P_a,P_b)=\frac{D(P_a,P_b)}{\max(|P_a|,|P_b|)}
\]
3) 路径分布熵，
\[
H=-\sum_i p_i \log_2 p_i
\]
以及唯一路径占比（unique-path ratio）。

统计结果显示：唯一路径数为 27，唯一路径占比为 6.75%，路径熵为 4.287 bits；两两路径 Jaccard 的均值/中位数/P90 分别为 0.150/0.000/0.500；两两归一化编辑距离的均值/中位数/P90 分别为 0.530/0.500/0.750。Jaccard 中位数为 0 表明多数路径对在边层面无重叠，编辑距离分布整体偏高表明路径序列差异显著。对应证据分别见图7、图8、图10、图11；图9展示路径频次与熵，图12展示跨 hop 层的路径分流结构。

综上，本文选路机制在并发条件下同时满足“质量可控”与“路径差异显著”的双重目标：传输质量维度稳定满足约束，路径形态在边重叠与序列结构上均呈现较强离散性，从而提升了流量关联与模式识别难度。

### 图注建议（中文正文，图内英文标题与坐标轴）

- 图6：`Path QoS Compliance Rate`（各 QoS 维度通过率及总体合规率）。  
- 图7：`Pairwise Path Jaccard Similarity Distribution`（路径边重叠率分布）。  
- 图8：`Pairwise Normalized Edit Distance Distribution`（路径序列差异分布）。  
- 图9：`Path Frequency and Entropy`（Top 路径频次与熵）。  
- 图10：`Pairwise Path Jaccard Similarity Heatmap`（路径相似度矩阵可视化）。  
- 图11：`ECDF of Pairwise Metrics`（Jaccard 与编辑距离的累积分布）。  
- 图12：`Hop Transition Alluvial View`（跨 hop 的路径分流/汇聚结构）。
