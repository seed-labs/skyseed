# 手动网络实验

[English](MANUAL.md) · [构建与运行](README.zh-CN.md)

先按[构建说明](README.zh-CN.md)准备好示例。已配置本机快捷入口时使用：

```bash
skyseed
cd examples/s4_handover
python run.py
```

网络默认循环已保存的600秒场景，按 Ctrl+C 停止。以下步骤对应默认 S4 配置。

## 进入节点

另开宿主终端：

```bash
skyseed
skytime
skynodes
docksh NAME
```

`skytime` 显示轮内时间和 UT 当前服务星。`skynodes` 显示 UT、关联 GS 的地址与容器名，
以及当前服务星。将列出的容器名交给 `docksh` 即可进入，`exit` 返回宿主。
`dockps` 列出运行容器。

## 发包

先选 Akita 观察星间转发。在 UT 中，将 `GS_IP` 替换为 `skynodes` 给出的地址：

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

任一端输入一行并回车，在另一端观察接收。保持这些终端，另开终端进入卫星观察。

## 观察接口与换星

在节点容器内执行：

```bash
ip -br addr
tcpdump -i any -nn
```

抓包显示当前容器的报文，MPLS 包中可以看到 label，Ctrl+C 停止抓包。
换星时，`ssut` 在旧服务星消失、在新服务星出现；UT 保持 `192.0.2.2`。
用 `skytime` 和 `skynodes` 找到当前服务星，`output/observations/handover-times.md`
给出本次计划的切换时刻，可与实际接入对照。

观察区域中断时，选择 Yamaguchi，重新开始 ping 或 nc 通信。
其配置的中断发生在每轮180–240秒，持续发送并观察中断前后的收发情况。

## 停止

在运行终端按一次 Ctrl+C，等待 UT 和网络清理。
`output/runs/<run>/` 保留执行、接入记录和诊断日志。

异常退出后，确认原运行已结束，再从仓库根目录执行：

```bash
docker compose -f examples/s4_handover/output/seed/deployment/docker-compose.runtime.yml -p skyseed-s4 down
docker ps -a --filter label=skyseed.example=s4-handover
docker ps -a --filter label=skyseed.runner
```

辨认本次运行的剩余容器后，用 `docker stop NAME`、`docker rm NAME` 清理。
