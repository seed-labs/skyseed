# 可视化前端的数据来源

[English](README.md)

SkySEED 用 Scene 描述有哪些卫星和地面站（GS），用 Docker 找到它们各自的容器，用 Runner 的执行回执更新运行中的拓扑。三者通过 Scene 的 `node_id` 关联。下面对应当前 `development` 的实现。

| 前端要显示什么 | 数据从哪里来 | 关联方式 |
|---|---|---|
| 容器名称、运行状态、终端 | Docker Engine API | 从容器标签取得 `node_id`，用 Docker `Id` 打开对应容器 |
| 卫星、轨道分组、GS 位置 | 内存里的 `Scene` 或该次实验保存的 `.scene` 文件 | 卫星和 GS 都有 `node_id` |
| 当前链路、Zone 负责卫星、实验时钟 | 运行地图后端提供的拓扑 JSON | 链路两端和负责卫星都引用节点 ID |

## 容器数据

可以沿用 SEED 的 Docker 查询和终端。SkySEED 增加的关联信息在 `org.seedsecuritylabs.seedemu.meta.skyseed.scene_id` 标签中，值就是 Scene 的 `node_id`。旁边的 `org.seedsecuritylabs.seedemu.meta.skyseed.scene_kind` 区分 `satellite` 和 `ground_station`。Docker 列表响应中标签位于 `Labels`，inspect 响应中位于 `Config.Labels`。

现有地图在启动时用 `Deployment.containers()` 取得本次 Compose 项目的容器并建立映射。点开某个节点时，后端才 inspect 该容器，并在运行中的容器里执行 `ip -j address` 读取实际接口和地址。前端可直接请求 `GET /api/v1/skyseed/node/{node_id}`，路径中的节点 ID 需 URL 编码。[部署容器查询](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/src/skyseed/runner/deployment.py) · [节点详情接口](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/tools/internet_map/app/backend/skyseed.js)

## 卫星和地面站数据

同一份 Scene 可以直接传给可视化，也可以在保存后用 `Scene.load(...)` 读取。两条路径得到保存时相同的节点、标识、分组和初始位置参数。Python 端用 `scene.get_satellites()` 和 `scene.get_ground_stations()` 分别取得两类节点。S4 将 Scene 写入 `examples/s4_handover/output/scene/scene.scene`。文件是 JSON，格式标识为 `skyseed.scene`，当前版本为 2，前端可以直接解析。[Scene 格式](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/src/skyseed/scene/README.zh-CN.md) · [S4 构造和保存 Scene 的代码](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/examples/s4_handover/scene.py)

| 对象 | 从保存的 Scene 读取 |
|---|---|
| 卫星 | 在 `nodes` 中选 `kind == "satellite"`，读 `node_id`、`name`。有轨道分组时，`orbit_id` 指向 `groups[].group_id`，沿 `parent_id` 可找到 shell 和星座 |
| GS | 在 `nodes` 中选 `kind == "ground_station"`，读 `node_id`、`name`、`latitude_deg`、`longitude_deg`、`elevation_m`。可选的 `group_id` 指向地面站分组 |

固定拓扑图用 Scene 分组计算画布坐标 `x/y`。如果要画卫星的地理位置，S4 另存了按时刻和节点 ID 索引的 ECEF 位置采样，单位为米，文件是 `output/observations/map-positions.json`。有采样时，节点详情返回 `position_m` 和对应的 `position_time`。[固定布局](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/tools/internet_map/layout.py) · [S4 位置采样](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/examples/s4_handover/simu.py)

## 运行时数据

运行 S4 并启用地图时，实验主机上的 `http://127.0.0.1:8080/api/v1/skyseed` 返回当前拓扑。传输方式是 HTTP。SEED 已有的 WebSocket 用于终端和抓包等功能。SkySEED 的拓扑周期推送尚未实现。前端现在可以参照下表理解数据，后续可协商是否复用这个 JSON 作为推送消息体。[HTTP 接口](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/tools/internet_map/app/backend/skyseed.js) · [现有前端读取和合并响应的代码](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/tools/internet_map/app/frontend/src/skyseed.mjs)

成功响应的外层是 `{"ok":true,"result":{...}}`。首次请求取得全量。此后，现有页面每隔约一秒携带上一次的运行标识 `run`（字符串）和拓扑版本 `revision`（整数）请求 `?run=<run>&since=<revision>`。

| `result` 中的字段 | 前端如何解释 |
|---|---|
| 全量中的 `nodes`、`links`、`zones`、`layout` | 用来建立整张图。`nodes` 以节点 ID 为键。`links[].ends[]` 中的 `node`、`name`、`addresses` 分别是该端节点 ID、接口名和地址列表 |
| `delta.links` | 以链路 ID 为键。值为链路对象时新增或替换，为 `null` 时删除 |
| `zones[id].ground_stations`、`zones[id].serving` | 前者是该 Zone 关联的 GS，后者是当前负责卫星。增量 `delta.zones[id]` 替换整个负责卫星列表，空数组表示当前无服务 |
| `phase`、`elapsed`、`seconds`、`service` | 实验阶段、总运行秒数、轮内秒数，以及可选的 UT 当前接入卫星。`service` 可为 `null` |

拓扑未变化时，增量可以为空，时钟仍会前进。重新启动实验（`run` 改变）或客户端版本过旧时，服务返回全量，前端据此替换旧图。[全量与增量的生成逻辑](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/tools/internet_map/app/backend/state.js)

**这些状态如何产生。** Runner 将已执行的操作记入本次运行的 `execution.jsonl`，配置 Store 时，也按顺序写入 etcd 的 `/<namespace>/runner/events/`。地图后端把链路回执的两端 ID 与编译目标中的接口、地址配对，再按已采用的路由更新 Zone 负责卫星。启用 Zone 展示取决于实验提供的关联数据。S4 的临时 UT 单独提供当前接入卫星。这样前端读到的是 Runner 已经执行的变化，无需自行解释 BPF 指令或周期扫描所有容器。[Runner 执行记录](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/src/skyseed/runner/runner.py) · [地图读取回执](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/tools/internet_map/app/backend/receipts.js) · [S4 的 UT](https://github.com/seed-labs/skyseed/blob/fea0ba97f8eca3aac53b2ae4b7d0f72ca0f200bc/examples/s4_handover/experiment/ut/map.py)
