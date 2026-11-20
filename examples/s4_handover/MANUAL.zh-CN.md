# 手动网络实验

[English](MANUAL.md) · [构建与运行](README.zh-CN.md)

先按[构建说明](README.zh-CN.md)完成开发环境和示例构建。已配置 `skyseed` 快捷入口时使用：

```bash
skyseed
cd examples/s4_handover
python map.py
python run.py
```

`map.py` 构建 SEED InternetMap2 的本例适配镜像；修改适配代码或重新计算计划后再执行。
后续直接 `python run.py` 即可。打开 [InternetMap2](http://localhost:8080/pro/map)。
网络默认循环 600 秒场景，运行终端按 Ctrl+C 停止。

## 在地图中操作

- 单击节点查看容器、身份 IP、接口与对端。卫星还显示轨道分组、位置采样和当前服务 Zone。
- 双击节点，或点详情中的 **Launch console**，进入该容器。
- 单击连线查看两端接口及链路参数，也可从这里进入任一端容器。
- UT 位于左侧，卫星按轨道面成环，GS 集中在右侧；区域按 Scene 规模生成，节点不能拖动。运行状态区显示轮内时间和 UT 当前服务星。

普通连线在选中节点后显示；勾选 **显示全部连接 / Show all links** 可查看全网。
UT 接入线保持显示。Filter 抓到流量时，对应链路会自动显现并高亮，最后一个包后保持 2.5 秒。

## 关注 Zone，从卫星或 GS 发包

选中 GS，在详情的 **选择关注的 Zone / Choose Zone** 中选择一个关联 Zone；
也可以从卫星详情选择它当前负责的 Zone。Zone 会出现在卫星环下方的固定关注区。
切换节点、打开终端或刷新网页都会保留关注项；在 Zone 详情点击 **取消关注 / Unwatch** 移除。
每次打开或刷新网页，都会在恢复关注列表时补上当前 UT 所属 Zone，避免旧缓存覆盖默认入口。
进入页面后仍可自由取消关注。

点击 Zone 查看它的 **Zone IP**、当前负责卫星和关联 GS。
紫色虚线表示 Zone 服务关系；换星时 Zone 位置不动，连线移到新的负责卫星。
详情中的 **Launch console** 分别进入当前负责卫星或对应 GS。
从 GS 向 Zone 发包：

```bash
ping ZONE_IP
```

从当前负责卫星向关联 GS 发包：

```bash
ping -I ZONE_IP GS_IP
```

Zone 详情提供填好 IP 的命令，点击 **复制 / Copy** 后粘贴到相应终端执行。
换星后，使用 Zone 详情进入新的负责卫星；已打开的终端仍留在原容器。

在服务星上也可以用 `nc -s ZONE_IP GS_IP 9090` 连接已监听的 GS。
反方向在服务星执行 `nc -l -p 9090`，关联 GS 执行 `nc ZONE_IP 9090`。
这里使用 Zone 地址作为业务源／目的；换星迁移地址与交付位置，nc 进程和 TCP 状态留在原卫星，
因此持续 TCP 的换星观察使用下面的 UT↔GS 步骤。

## 从 UT 发包

UT 详情列出三个关联 GS 及其 IP。先选 **Akita** 观察星间转发，将下面的
`GS_IP` 替换为它的地址，在 UT 中执行：

```bash
ping GS_IP
```

建立 TCP 时，在选定 GS 的终端执行：

```bash
nc -l -p 9090
```

在 UT 的终端执行：

```bash
nc GS_IP 9090
```

任一端输入一行并回车，在另一端观察接收。保持这两个窗口，继续从地图进入卫星观察底层连接。

## 观察换星和流量

在节点终端查看接口或抓包：

```bash
ip -br addr
tcpdump -i any -nn
```

`tcpdump` 显示当前容器各接口的报文，MPLS 包中可以看到 label；按 Ctrl+C 停止。
地图的 **Filter** 输入 `not ip6` 并 Submit，经过流量的节点和连线会闪烁，展开 **Log** 查看
完整节点名和抓包文本。这会保留 IPv4、ARP 和 MPLS，排除 Linux 接口自动产生的 IPv6 背景流量。
`all` 包含这些背景流量；`icmp`／`tcp` 适合看普通 IP 段，标签封装后的中转段用
`ether proto 0x8847`。当前 `any` 抓包环境不支持 `mpls` 过滤关键字。清空 Filter 后 Submit 停止抓包。

需要留下手动发包的记录时，在 Log 点击 **开始录制 / Record**，进行 ping／nc 操作，
再点击 **停止录制 / Stop recording → 保存记录 / Save**，下载 JSONL。
记录包含包文本、label、容器和最近状态中的接口／对端，方便按时间对照各节点的观察。
刷新页面、clear 或开始新录制前先保存；运行目录的 `execution.jsonl` 继续记录网络变更，包记录由浏览器保存。

换星时，UT 连线移到新服务卫星。点开旧、新卫星，用 `ip -br addr` 可以看到
`ssut` 在旧卫星消失、在新卫星出现。UT 的 `192.0.2.2` 地址保持不变。
单击当前接入连线，可以对照两端接口和地址。

观察区域中断时，将通信目标换为 **Yamaguchi**，重新建立 ping 或 nc 通信。
中断发生在每轮 180–240 秒，受影响的 GS–卫星接口及地图连线消失，恢复时重新出现。
在 UT／GS 的 nc 窗口继续输入，观察中断前后收发情况。

## 宿主快捷命令

需要在宿主查看时，另开终端使用：

```bash
skyseed
skytime
skynodes
docksh NAME
```

`skynodes` 显示 UT、三个 GS 和服务星的容器名；`dockps` 列出运行容器。
进入容器后用 `exit` 返回宿主。

## 停止

在运行终端按一次 Ctrl+C，等待 UT、地图和网络清理。
`output/runs/<run>/` 保留执行、接入和地图状态及诊断日志。

异常退出后，确认原运行已结束，再从仓库根目录执行：

```bash
docker compose -f examples/s4_handover/output/seed/deployment/docker-compose.runtime.yml -p skyseed-s4 down
docker ps -a --filter label=skyseed.example=s4-handover
docker ps -a --filter label=skyseed.runner
```

辨认本次运行的剩余容器后，用 `docker stop NAME`、`docker rm NAME` 清理。
