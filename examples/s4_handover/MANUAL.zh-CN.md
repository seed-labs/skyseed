# 手动网络实验

[English](MANUAL.md) · [构建与运行](README.zh-CN.md)

先完成[安装](../../README.zh-CN.md#安装)，再按主说明生成 Scene、Emu 和计划。
之后只需 `run`，不用每次重建。以下命令在自己克隆的仓库根目录、Bash 终端中执行。

```bash
conda activate skyseed_dev
source tools/deployment/shell.sh
python -m examples.s4_handover.run
```

长期使用可按 [Tools 说明](../../tools/README.zh-CN.md#快捷命令)配置终端快捷入口。
脚本随仓库提供，不依赖作者工作站的私人配置。

## 时间与对象

另开终端，进入仓库根目录并加载环境和命令：

```bash
conda activate skyseed_dev
source tools/deployment/shell.sh
skytime       # 当前阶段、总时间、轮数、轮内时间、干扰和服务星
skynodes      # UT、默认 GS、两个备用 GS、当前服务星的容器名和 IP
dockps        # 所有运行容器的短 ID 与名称
docksh 容器名 # 在选定容器中打开 Bash；退出用 exit
```

`run` 终端每秒更新状态。每轮 600 秒；180–240 秒为干扰窗口。
`run.py` 的 `REPEAT = False` 改为只执行一轮；`CAPTURE = True` 保存端点 PCAP。
默认手动模式不持续写抓包，按需要在下方指定接口抓取即可。

| 对象 | 选择方法 | 用途 |
|---|---|---|
| UT | `skynodes` 的 UT 行 | 固定 `192.0.2.2`，运行客户端或监听服务 |
| 默认 GS | GS 1，当前配置为 Yamaguchi | 自动 TCP echo 使用这里；端口 2323 已占用 |
| 备用 GS | GS 2 Akita、GS 3 Hitachinaka | 手动测试用同样的流程，不会自动接管已有 TCP 会话 |
| 当前服务星 | service 行 | 查看 UT 接口、末端规则与骨干网接口 |

三个 GS 按 Zone 中心的球面距离排序，路径不保证始终存在。GS 的实际节点 IP 由 Compiler
分配，以 `skynodes` 为准。其他未关联的 GS 没有本例的 UT 前缀接入规则，不能据此测试任意 GS。

## 双向 nc

将 `skynodes` 输出的容器名、GS IP 代入。两个容器各开一个终端：

| 位置 | 命令 | 观察 |
|---|---|---|
| 任一关联 GS 容器 | `nc -l -k -p 2324` | 监听；使用 2324 避开自动 echo |
| UT 容器 | `nc -v GS的IP 2324` | 连接后输入文字并回车，GS 应收到；反向输入也可收到 |
| UT 容器（反向建连） | `nc -l -k -p 2324` | 作为服务端 |
| 任一关联 GS 容器 | `nc -v 192.0.2.2 2324` | 发起反向连接 |

`-k` 允许服务端继续接受后续连接；客户端退出后不会自动重连，需要重新执行 `nc`。
断链时已建立的 TCP 可能等待重传，网络恢复后也可能继续等退避计时器；这与连接已经关闭不同。
用 `ss -tn` 区分状态，不能仅看终端暂时没有输出。
默认 GS 的自动 echo 是独立程序，回复日志在当前 run 的 `tcp-client.jsonl`。

## 换星时间与接口变化

每次 `simu` 从实际规划结果生成本例目录下的 `output/observations/handover-times.md`。
此轮内换星时间表在本地构建后查看，不随源码提供。
该表给出计划服务星，不冒充实际生效时间；运行时以 `skytime`、`attachment.jsonl` 和回放为准。
每轮的对应时刻重复出现，采用与接入可能稍晚；初始接入还要等待可用路径。

| 时机 | 建议观察 | 预期现象 |
|---|---|---|
| 首次接入 | UT：`ip -br addr`、`ip route` | `ssutidentity` 持有 `192.0.2.2/32`；出现 `ssut` 接口及三个 GS 的 /32 路由 |
| 换星前后 | UT：`ip -o link show ssut` | 名字仍是 `ssut`，但删除重建后的 ifindex/peer ifindex 会变化 |
| 换星前后 | 旧、新服务星：`ip -br link`、`ip route show 192.0.2.2/32` | 旧星失去 `ssut`、UT 路由变为丢弃；新星获得 `ssut` 和本地交付路由 |
| 换星前后 | UT/GS：`ss -tn` | 容器、UT/GS IP、同一连接两端地址保持；短暂停顿不等于新建连接 |
| 180–240 秒 | 受影响 GS：`ip -br link` | 圈内星地 veth 被撤销；恢复时接口名可相同，但编号通常改变 |
| 每轮边界 | 终端轮数、接口及回放 | 只更新起点与终点不同的链路/规则；可能短暂断流，属于人工回跳 |

UT 接入的链路地址固定为服务星 `169.254.254.1/30`、UT `169.254.254.2/30`。
每次换星保留业务 IP，但重建 veth。骨干接口由 Compiler 命名，可用 `ip -br link` 查实际名称。
BPF 标签表不是 Linux MPLS/IP FIB，`ip route` 不会列出中间星的全部标签动作；规则详情看执行回放。

```bash
# 容器内：接口、路由、连接与抓包
ip -br addr
ip route
ss -ltn             # 监听端口
ss -tn              # TCP 会话
# UT：原始 IPv4 TCP；Ctrl+C 结束抓包
tcpdump -ni ssut 'tcp port 2324'
# GS：先用 ip -br link 找到动态接口；标签在 MPLS 帧中
tcpdump -nei 接口名 'ether proto 0x8847'
```

## 停止与产物

在运行 `run` 的终端按一次 Ctrl+C，等待资源释放和结果写出。它会保留当前轮已完成的记录，
关闭本次 UT、测量、Runner、Compose 和 etcd；不会重置或停止机器上的其他实验。
输入冻结在 `output/runs/<run>/inputs/`，执行和接入记录在同目录，结果在 `views/`。
循环只复用一份计划，真实执行时钟持续向前；不是超过 600 秒的连续轨道传播。

异常退出时，先确认原 `run` 已结束，再针对本例清理：

```bash
docker compose -f examples/s4_handover/output/seed/deployment/docker-compose.runtime.yml -p skyseed-s4 down
docker ps -a --filter label=skyseed.example=s4-handover
docker ps -a --filter label=skyseed.runner
# 从上面的清单核对属于这次 run 的容器，然后分别停止；不要全局 prune。
docker stop 具体容器名
```

临时 UT 主容器不是 `--rm` 容器，停止后再用 `docker rm 具体UT容器名` 删除。
独立打开的 Internet Map 不属于 Runner，可继续保留；关闭用 `docker stop seedemu_internet_map`。
通用的 `dcbuild/dcup/dcdown` 在目标 Compose 目录执行；`dcup` 只部署容器，不会启动 Runner。
