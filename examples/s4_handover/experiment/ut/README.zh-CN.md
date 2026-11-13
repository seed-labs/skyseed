# 本例 UT 接入

[English](README.md) · [实验入口](../README.zh-CN.md)

UT 是独立容器，为本例提供稳定应用地址和真实 TCP 测量。它不属于 Scene/Emu 的卫星
或 GS，也不是框架默认的终端移动性协议。当前使用原始 IP 包加标签，无 IP-in-IP。

```mermaid
flowchart LR
    UT["同一个 UT 容器<br/>192.0.2.2"] -->|可变接入 veth| Sat[实际采用 pop 的服务卫星]
    Sat -->|标签转发原始 IP 包| Net[真实 BPF 卫星网络]
    Net --> GS["GS<br/>计划中的稳定节点地址"]
```

| 文件/对象 | 职责 |
|---|---|
| `routing.py:TerminalRoutingModel` | 默认 GZ 路由，在每个关联 GS 增加一条入口规则，把 UT /32 映射到已有 Zone 标签路径 |
| `attachment.py:TerminalExperiment` | 拥有临时 UT 容器、接入和测量进程；退出时等待并清理 |
| `attachment.py:Attachment` | 建立接入 veth 和临时路由；按实际采用的服务规则换星 |
| `traffic.py` | TCP 服务端/客户端各建立一次连接，发送并核对编号回显 |

```python
network.set_routing_hook(TerminalRoutingModel, model_options={"ground_stations_per_zone": 3})
# 保存并编译计划，然后在运行阶段使用：
with TerminalExperiment(scene, deployment, plans, run_output, seconds=600,
                        ground_stations=build["experiment"]["ground_stations"], capture=True):
    runner.run()
```

## 地址与包路径

| 身份 | 本场景地址 | 位置/作用 |
|---|---|---|
| UT 应用 | `192.0.2.2/32` | Scene 的实验配置；UT 的 `ssutidentity`，换星时不变 |
| GS 应用 | `skynodes` | 从计划读取 GS 的稳定节点地址，Runner 管理 |
| Zone 身份 | `PhysicalPlan.targets` | 默认计划的逻辑端点地址，仍由 Runner 管理；不作为外层包头 |
| 临时接入 | 星侧 `169.254.254.1/30`、UT 侧 `.2/30` | `ssut` veth 的下一跳地址，与应用地址分开 |

GS/Zone 的具体地址随编译分配，例子从计划读取，不把数字写死到配置过程。
UT 用 `--network none` 创建，仅经接入 veth 与卫星网络通信。

```text
星间帧：Ethernet | MPLS label | 原始 IPv4 | TCP
正向 IP：192.0.2.2 → GS 节点地址
反向 IP：GS 节点地址 → 192.0.2.2
```

正向包在 UT 由一条指向 GS 的路由送到接入 veth。服务星针对 `ssut` 入站、目的 GS
的策略规则把它送入 Runner 的 BPF 入口 `sslocalout → sslocalin`；BPF 按目的选择
第一跳标签。中间星按本地入标签换标，GS 弹标后交给原始目的地址上的应用。

反向包的目的保持 UT 地址。`TerminalRoutingModel` 只在 GS 增加一条前缀入口，复用
已有 Zone 路径的出口和下游标签。中间星不需要 UT IP 路由，也不因该入口改变标签。
当前服务星弹标后，Linux 看到目的 `192.0.2.2`，用临时 `/32` 路由交给接入 veth。
Zone 的本地身份与 UT 地址不同，原始包不会被错误留在卫星的本地应用栈。

换星按实际 `pop_deliver` 采用回执进行，不提前使用未来计划。旧接入删除，UT 路由
恢复为丢弃；新接入建立后在新服务星安装 UT `/32` 路由。UT 容器、地址和 socket
保持不变。未接入时丢弃，避免流量落到无关默认路由；没有 NAT、隧道或重连。

## 标签与测量

| 阶段 | 行为 |
|---|---|
| 入口 | 根据原始 IP 目的选择标签路径；BPF 决策，TC 插入标签头 |
| 中间星 | BPF 按本地入标签查表、换成下游标签、递减标签 TTL 并转发 |
| 末端 | BPF 决定弹标，TC 删除标签头，再由 Linux 交给本地应用或 UT 路由 |
| 断链 | 逻辑端口绑定缺失时丢包，保留仍有效的路由意图 |

没有路由协议守护进程或 Linux MPLS FIB。临时 veth 没有独立的 UT 无线链路预算、
时延或丢包；本例不验证完整无线终端移动性。

TCP 服务绑定 GS 节点地址的 2323 端口，只 accept 一次；UT 只 connect 一次，发送
`skyseed 序号\n` 并检查原样回显，没有自动重连。它是普通 TCP echo，不是包含协商、
终端控制或登录的 Telnet 服务。单连接服务器不能同时作为第二个交互会话。

TCP 内核在丢包后重传，连续超时增加等待；网络恢复不会立即重置计时器，应用可能继续等待重传。机制见 [RFC 6298 §5](https://www.rfc-editor.org/rfc/rfc6298.html#section-5)。
比较两端 PCAP、socket 与执行时刻区分 TCP 退避和 Runner 滞后；端点抓包不代表逐跳观测。

TCP JSONL 写入本次 run，PCAP 仅在 capture=True 时保存。客户端在首次实际接入后启动，换星不重启。
始终没有接入时，客户端保留未启动状态。容器、辅助进程、接口及路由由接入对象拥有并按顺序清理。
