# 临时 UT 接入

[English](README.md) · [实验说明](../README.zh-CN.md)

本例适配创建一个具有稳定业务地址的 UT 容器，并将它接入当前服务其 Zone 的卫星。
接入跟随实际路由采用回执，支持换星及计划循环。业务程序和抓包由用户自行启动。

```mermaid
flowchart LR
    UT["UT · 192.0.2.2"] -->|变化的接入 veth| Sat[当前服务卫星]
    Sat -->|原始 IP 上的 MPLS| Net[卫星网络]
    Net --> GS[关联 GS]
```

| 文件／对象 | 职责 |
|---|---|
| `routing.py:TerminalRoutingModel` | 在关联 GS 为 UT /32 增加入口分类，复用所属 Zone 的标签路径 |
| `attachment.py:TerminalAttachment` | 拥有 UT 容器和一个接入助手，退出时等待清理 |
| `attachment.py:Attachment` | 根据已采用的服务规则维护接入 veth 和临时路由 |

```python
network.set_routing_hook(TerminalRoutingModel, model_options={"ground_stations_per_zone": 3})
# 保存并编译计划后，在运行阶段：
with TerminalAttachment(scene, deployment, plans, run_output,
                        ground_stations=build["experiment"]["ground_stations"]):
    runner.run()
```

循环运行时传入与 Runner 共用的 `repetition` 对象。
助手只打开 UT 与可能服务它的卫星网络空间，不在 GS 容器中启动程序。

## 地址与转发

| 地址 | 用途 |
|---|---|
| UT 的 `ssutidentity` 上 `192.0.2.2/32` | 换星前后不变的业务身份 |
| `skynodes` 给出的 GS 地址 | Compiler 分配的节点身份，用于手动通信 |
| 卫星 `169.254.254.1/30`、UT `169.254.254.2/30` | 临时 `ssut` veth 的接入下一跳 |

UT 发出的包保留原始 IP 地址。服务卫星压入标签，中间卫星交换标签，目标 GS 弹出并交付。
回复由 GS 的 UT /32 分类进入 Zone 标签路径，最后通过服务卫星的临时路由交给 UT。
没有 IP-in-IP 或地址转换。

服务变化时，适配器删除旧接入 veth，建立新的接入。UT 容器和业务地址不变，应用可以保持
原有 socket。没有服务卫星时，接入路由丢弃流量。换星由实际采用回执驱动，不能只看计划时刻。
若整个计划都没有 UT 服务星，启动时会提示，UT 保持未接入，其他网络与地图继续运行。
`terminal.json` 的 `ground_ips` 只列出计划中有节点身份地址的关联 GS；全部关联 GS 可在地图的 Zone 详情中查看。

这是本例的临时适配，不是通用 UT 移动协议，也没有独立的无线链路预算、延迟或丢包模型。

## 运行状态与清理

`terminal.json` 保存 UT、关联 GS、候选服务容器及助手配置；
`attachment-state.json` 保存当前服务节点。
`attachment.jsonl` 与 `terminal-helper.log` 记录接入变化和错误；
Runner 的 `execution.jsonl` 提供已采用规则事件。

适配器拥有并清理自己的容器、助手、接口和临时路由，也处理准备中途失败的资源。
它不启动 echo 服务、客户端或 tcpdump，不归档测量或生成结果页面。
手动指令见[操作手册](../../MANUAL.zh-CN.md)。

地图对本例 UT 文件的依赖集中在 [map.py](map.py)。
开发正式 UT 时可分别替换接入实现与这层显示对接；固定位置、单 UT 和 `ssut` 是本例约定，
不要求正式系统沿用这些文件、位置或状态格式。
