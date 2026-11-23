# 运行地图

[English](README.md) · [S4 操作手册](../../examples/s4_handover/MANUAL.zh-CN.md)

运行实验时打开 [InternetMap2](http://localhost:8080/pro/map)。单击节点查看接口和对端，双击进入容器。Filter、Log 和手动录制沿用 SEED 的操作。选中 GS 或卫星可关注相关 Zone，换星时服务线随 Runner 的采用回执更新。

## 接入实验

```python
from skyseed import Runner
from tools.internet_map import InternetMap, ensure_image

ensure_image()
with deployment, Runner(iter(plans), deployment, output=run_dir, store=store) as runner:
    with InternetMap(scene, deployment, plans, run_dir, store=store):
        runner.run()
```

`plans` 是按 Runner 顺序保存、可重复迭代的有限物理计划。部署和 Runner 准备好后进入地图上下文，退出时释放地图容器。默认端口是 8080，可用 `port` 修改。实验提供自己的 Store 时地图订阅同一命名空间的 etcd 回执。省略 `store` 时地图增量读取同一 Runner 的 `execution.jsonl`。

| 显示内容 | 数据来源 |
|---|---|
| 卫星环、GS、固定位置 | Scene 的节点和轨道分组 |
| 图上的节点与终端 | 启动时用 Scene ID 匹配本次部署的 Docker 容器，包括未进入计划的 GS |
| 链路、接口名、地址、方向参数 | 成功执行的链路回执及其对应的编译目标和指令 |
| 容器详情 | 选中节点时读取该容器状态及接口。已打开详情的节点发生建链或断链时刷新 |
| 当前负责卫星 | 可选的 GZ 关联资料及该卫星已采用的本地交付规则 |
| 卫星空间位置 | 可选 `positions` 文件中轮内时刻之前最近的 ECEF 采样 |
| 抓包 | 启用 Filter 后由容器中的 tcpdump 采集。Log 可保存手动录制的 JSONL |

规划时用 `zone_associations(scene, routing_model)` 取得可保存的关联表。它识别默认 GZ 路由模型及其子类，其他模型返回空表。运行时通过 `zones=associations` 传入。普通卫星和 GS 地图可以省略 Zone、UT 和位置采样。S4 另外传入示例自己的 [UT 接入显示](../../examples/s4_handover/experiment/ut/map.py)。循环运行的示例提供首轮及一轮含跨界操作的计划索引和周期秒数。

## 源码与构建

- `layout.py` 生成固定区域。`runtime.py` 连接部署和编译信息，管理地图容器。
- `app/backend/receipts.js` 读取 Runner 回执。`state.js` 将回执应用于拓扑并提供增量更新。`skyseed.js` 提供页面的状态及节点详情接口。
- `app/frontend/src/skyseed.mjs` 控制图、节点详情和 Zone 选择。`capture.mjs` 关联抓包、接口和连线。
- `app/` 包含基于 [SEED InternetMap2](https://github.com/seed-labs/seed-emulator/tree/80433bf813c8f9be23d4d9c7c9f7c914b5132da9/tools/InternetMap2) 的适配源码和[上游许可证](app/LICENSE.txt)。`build_image()` 从此目录在本机构建 `skyseed-internet-map`。更新地图代码后在 S4 目录执行 `python map.py`，更换 Scene 或计划复用同一镜像。

地图运行在 Linux 主机网络上，HTTP 仅监听回环地址。容器通过 Docker socket 提供终端、节点详情和抓包，通过只读挂载读取本轮资料。日常刷新传输上次版本以来变化的链路和 Zone，首次打开或断开较久后取得全量图。手动在容器中调整接口时，可重新点开该节点查看内核接口。
