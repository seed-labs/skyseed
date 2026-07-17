# v1 S4 静态场景定向调查

## 1. 调查边界

本文只回答一个问题：在不复用 v1 架构的前提下，v1 的标准 S4 场景静态上包含什么，以及这些事实如何成为 Skyseed v2 当前 Emulator 对象模型的候选输入。

调查包含：Satellite、Orbit、GroundStation、Skyseed AS、普通地面 AS、IX、Network、Router、静态 peering、静态地址，以及节点上可观察到的软件/文件/服务事实。调查不把 v1 的类、模块、Runner、Planner、Compiler、runtime context、helper 调用链、可变字典或运行控制流视为 v2 设计依据。

以下内容明确不属于本轮输入：UT、时间范围、slice/horizon interval、动态位置、可见性、ISL、GS–Satellite link、Zone、G/Z 路由、qdisc、BPF maps、generation、preload、slot、MPLS、运行资源清单及动态后端资源。只在需要判定某个值是否真正物化时引用运行产物，不调查这些机制本身。

本文使用以下分类：

| 分类 | 本文含义 |
| --- | --- |
| `EMULATOR_STATIC_REQUIRED` | 重建该静态场景不可缺少的输入，或必须明确为空的场景集合 |
| `EMULATOR_STATIC_OPTIONAL` | 静态且有证据，但不是创建基本 Emulator 实体的必要条件 |
| `SIMULATOR_DYNAMIC` | 随时间或拓扑计算变化，应留给 Simulator |
| `BACKEND_RUNTIME` | 只在容器、网络命名空间或运行后端物化 |
| `V1_IMPLEMENTATION_ARTIFACT` | v1 实现或产物布局造成，不应进入 v2 场景合同 |
| `UNCERTAIN` | 证据冲突或语义需要用户裁决 |

本文中的“当前标准 S4”专指 `examples/s4_operational_clean/experiment.py` 选择、并由当前成功产物验证的场景，不把文件名中含有 S4 的工具示例、缩短回放审计或历史迁移文档当作另一份静态场景定义。证据见 `skyseed_v1/examples/s4_operational_clean/experiment.py:14-25` 的 `EXPERIMENT`、`skyseed_v1/examples/s4_operational_clean/README_EN.md:6-22`，以及 `skyseed_v1/docs/operations/validation_status_EN.md:38-61`。

## 2. v1 commit 和相关配置入口

### 2.1 调查快照

| 项目 | 值 | 证据 |
| --- | --- | --- |
| 调查时仓库 HEAD | `274f5b22880efc9bc212bd18c6d47968d25263dd` | `git rev-parse HEAD` |
| 最后修改 `skyseed_v1` 的 commit | `06b19eb718a79a9113ee95bc045f10953c1d8ab7` | `git log -1 --format=%H -- skyseed_v1` |
| 该 commit 的 v1 tree | `23d016333325ddd365c16dd17deec29666ba16f4` | `git rev-parse 06b19eb718a79a9113ee95bc045f10953c1d8ab7:skyseed_v1` |
| 调查时 HEAD 的 v1 tree | `23d016333325ddd365c16dd17deec29666ba16f4` | `git rev-parse HEAD:skyseed_v1` |
| 最后修改 S4 入口/轨道目录/GW binding 的 commit | `3b33c369ff88ee7a790a6ea731c0afc3af403552` | `git log -1 --format=%H -- skyseed_v1/examples/s4_operational_clean/experiment.py skyseed_v1/data/orbits/planned_shell_orbit_data_s1_s5.json skyseed_v1/data/ground/s4_visualization_gateway_binding_12.json` |

因此本文报告的 **v1 commit 是 `06b19eb718a79a9113ee95bc045f10953c1d8ab7`**；调查时 HEAD 含有完全相同的 v1 tree。成功产物目录自身没有内嵌 git commit，产物与 commit 的对应关系只能通过未变化的静态源和产物内容交叉验证，不能伪装成产物自证的精确 commit。

### 2.2 标准入口及来源层级

1. 用户可见的标准组合入口是 `skyseed_v1/examples/s4_operational_clean/experiment.py:14-25`：`EXPERIMENT` 显式选择 `planned_shell("S4")` 和 `s4_rich_ground_network()`。
2. shell 数量配置来自代码常量 `skyseed_v1/src/skyseed_v1/configuration/presets.py:6-10` 的 `SHELLS["S4"] = ShellConfig("S4", 6, 58, 190)`。其中 `190` 是动态跨轨候选 slot 数，不是静态星座规模输入。
3. altitude 和 inclination 来自代码常量 `skyseed_v1/src/skyseed_v1/topology/shell.py:43-49` 的 `PLANNED_SHELL_PRESETS["S4"]`；`skyseed_v1/src/skyseed_v1/visualization/export_planned_shell_orbit_data.py:71-77` 有相同的导出参数。
4. 每颗卫星的确定性轨道目录来自 `skyseed_v1/data/orbits/planned_shell_orbit_data_s1_s5.json`；其 `metadata` 在 `:1-14`，S4 记录位于 `:58335-63553`。
5. 地面预设来自 `skyseed_v1/src/skyseed_v1/configuration/presets.py:21-39` 的 `s4_rich_ground_network`；12 个 GW 的显式绑定来自 `skyseed_v1/data/ground/s4_visualization_gateway_binding_12.json:1-182`，位置原始目录来自 `skyseed_v1/data/ground/starlink_gateways.json`。
6. 真实物化交叉检查使用成功产物 `skyseed_v1/examples/s4_operational_clean/output/rich_s4_operational_clean_bpf-20260713T033419Z/`。该目录是证据，不是新的配置入口。

## 3. S4 卫星静态配置

### 3.1 结论

S4 是一个单 shell 参数组：**6 个轨道面，每面 58 颗，共 348 颗 Satellite，altitude 560.0 km，inclination 97.6°**。没有第二组不同 altitude/inclination 参数。标准入口没有小于完整规模的截断上限；resolved `max_satellites=348` 由完整规模派生，348 不是独立的规模旋钮（`configuration/resolver.py:35-40`）。

直接证据：

- `skyseed_v1/src/skyseed_v1/configuration/presets.py:8-10` 的 `SHELLS["S4"]` 声明 `plane_count=6`、`satellites_per_plane=58`。
- `skyseed_v1/src/skyseed_v1/configuration/models.py:10-19` 的 `ShellConfig` 及 `satellite_count` 表明总数是两者乘积。
- `skyseed_v1/src/skyseed_v1/topology/shell.py:43-49` 的 `PLANNED_SHELL_PRESETS["S4"]` 声明 `altitude_km=560.0`、`inclination_deg=97.6`。
- `skyseed_v1/examples/s4_operational_clean/README_EN.md:6-22` 明确称其为完整 S4，并说明 348 是 shell 定义派生值、不得被静默截断。
- 未执行的合同测试 `skyseed_v1/tests/integration/test_s4_example.py:17-26` 也断言 `planned_shell == "S4"`、`max_satellites == 348`；这里只把测试源码作为声明证据，不把它当作本轮运行结果。

### 3.2 轨道面和轨内分布规则

`skyseed_v1/src/skyseed_v1/topology/shell.py:181-223` 的确定性目录生成规则给出以下静态事实：

| 项目 | 规则或值 | 类型 |
| --- | --- | --- |
| plane 索引 | 内部零基 `p = 0..5`；公开标识一基 `1..6` | 确定性派生 |
| RAAN | `360 × p / 6`，即 `0, 60, 120, 180, 240, 300` 度 | 确定性派生 |
| satellite 索引 | 内部零基 `s = 0..57`；公开标识一基 `1..58` | 确定性派生 |
| 基本 mean anomaly 步长 | `360 / 58 = 6.2068965517...` 度 | 确定性派生 |
| 相邻 plane 相位 | 零基偶数 plane 偏移 `0`；零基奇数 plane 偏移 `180 / 58 = 3.1034482759...` 度 | 确定性派生 |
| mean anomaly | `(360 × s / 58 + 180 × (p mod 2) / 58) mod 360` | 确定性派生 |
| NORAD 起点 | S4 preset index 为 4，base 为 `940000` | 确定性派生 |
| NORAD | `940000 + p × 58 + s + 1`，范围 `940001..940348` | 确定性派生 |
| Satellite 显示名 | `STARLINK-S4-P{plane:02d}-S{satellite:02d}` | 确定性派生 |
| v1 Satellite 节点 ID | `stl-<norad>`，即 `stl-940001..stl-940348` | 确定性派生；`skyseed_v1/src/skyseed_v1/topology/shell.py:52-54` |
| Orbit ID | `shell-plane-{plane:03d}`，即 `shell-plane-001..shell-plane-006` | 确定性派生；`skyseed_v1/src/skyseed_v1/topology/shell.py:265-303`，尤其 `:296` |

目录首条 S4 记录 `skyseed_v1/data/orbits/planned_shell_orbit_data_s1_s5.json:58335-58354` 是 `STARLINK-S4-P01-S01`、NORAD `940001`、RAAN `0`、mean anomaly `0`。末条 `:63538-63553` 是 `STARLINK-S4-P06-S58`、NORAD `940348`、RAAN `300`、mean anomaly `356.896552`。该文件中 `satellite_name` 以 `STARLINK-S4-` 开头的记录实际为 348 条。

### 3.3 显式输入、确定性生成和动态数据

| 数据 | 来源 | 处理结论 |
| --- | --- | --- |
| shell 选择 `S4` | `examples/s4_operational_clean/experiment.py:18` | 用户显式配置；候选输入 |
| plane 数、每面 Satellite 数 | `configuration/presets.py:8-10` | 由 shell preset 显式确定；候选输入 |
| altitude、inclination | `topology/shell.py:43-49` | 代码常量；候选输入 |
| RAAN、mean anomaly、Orbit/Satellite 标识 | `topology/shell.py:181-223,265-303` | 可由规则确定性生成；候选输入可保存“规则”而非 348 行复制 |
| 总 Satellite 数 | `configuration/models.py:10-19` | 派生字段，只做一致性校验 |
| reference epoch `2026-01-01T00:00:00Z` | `topology/shell.py:181-188` | 固定的轨道目录生成参考，不是运行时间范围；可选保存以解释 mean anomaly，但不是当前 Emulator 的必填输入 |
| mean motion `15.045579289 rev/day` | `topology/shell.py:189-223` 及轨道目录记录 | 由 altitude 确定性计算；可作派生校验值，不是独立输入 |
| 时间位置、可见性、ISL、slot | 不属于静态实体事实 | `SIMULATOR_DYNAMIC` 或 `BACKEND_RUNTIME`，排除 |

S4 Satellite 的所有者 ASN 是 **250**；GS 所有者 ASN 是 **65000**。常量证据为 `skyseed_v1/src/skyseed_v1/adapters/seedemu/integration.py:37-38`。成功 Compose 中 Satellite `rnode_250_stl-940001` 的标签也显示 ASN 250，见 `.../seed_output/docker-compose.yml:687-712`。

### 3.4 数量与产物交叉检查

| 检查层 | 结果 | 证据 |
| --- | --- | --- |
| 配置声明 | `6 × 58 = 348` | `configuration/presets.py:8-10`、`configuration/models.py:10-19` |
| 轨道数据实际记录 | 348，NORAD `940001..940348` | `data/orbits/planned_shell_orbit_data_s1_s5.json:58335-63553` |
| 成功 shell manifest | `record_count=348`、6 plane、每面 58 | `.../shell_manifest.json:20342-20354,20785-21127` |
| 成功 topology 汇总 | 348 Satellite、6 plane | `.../topology.json:2029-2035` |
| 成功 Compose 服务 | `rnode_250_stl-*` 实际 348 个 | `.../seed_output/docker-compose.yml` 中服务名和标签 |
| 预期容器公式 | `348 + 2×28 + 12 = 416` | `.../effective_profile.json:153` 与 `configuration/resolver.py:17-21`；UT 只用于解释公式，不进入本轮输入 |

这些层次在 Satellite/Orbit 数量上没有冲突。`shell_manifest.json:20358` 还出现 `cluster_altitude_bucket_km=550.0`，但这是 25 km 聚类桶，不是 S4 altitude 覆盖；同一 manifest 的轨道记录、输入 preset 和 mean motion 与 560.0 km 一致。因此 S4 altitude 仍是 560.0 km，550.0 只记为 v1 产物统计字段。

## 4. GroundStation 静态配置

### 4.1 数量、选择和来源格式

标准 S4 使用 **12 个 GroundStation 候选实体**，ID 为 `gw201..gw212`，全部属于单一 ASN **65000**。不存在多个 GS AS。

- `skyseed_v1/src/skyseed_v1/configuration/presets.py:21-39` 的 `s4_rich_ground_network` 指定 `gateway_binding_catalog` 并显式列出 `gw201..gw212`；`skyseed_v1/src/skyseed_v1/configuration/resolver.py:35-48` 再从这个 12 项 tuple 派生 `max_gateways=12`。
- 标准位置目录 `skyseed_v1/data/ground/starlink_gateways.json` 是 JSON list，实际有 250 项；每项具有 `id`、`name`、`city`、`longitude`、`latitude`、`altitudeMeters`。
- `skyseed_v1/data/ground/s4_visualization_gateway_binding_12.json:1-182` 是独立 JSON list，把 12 个场景 ID 绑定到标准目录中的 12 项。因此 S4 使用的是**显式绑定子集**，不是标准 250 项完整列表，也不是文件中的前 12 项。
- 静态标准化结果的证据位于 `skyseed_v1/src/skyseed_v1/models/ground.py:235-279`；其中类型是 `starlink_ground_station`，来源是 `visualization_starlink_gateways`，高度从 meter 转换为 km。`models/ground.py:319-359` 给出绑定项所需字段。
- 成功产物 `.../ground_manifest.json:903-969` 记录 `gateway_catalog_count=12`、`selected_gateway_count=12` 和 `gw201..gw212`；Compose 中实际也有 12 个 `rnode_65000_gw*` 服务。

### 4.2 12 个 GS 的静态字段

| GS ID | 显示名称 | 纬度 | 经度 | 海拔 m | service /32 | binding 中的旧 IX/upstream | 位置证据 |
| --- | --- | ---: | ---: | ---: | --- | --- | --- |
| `gw201` | Starlink Gateway - Colburn, ID | 48.4 | -116.55 | 0 | `198.18.1.1/32` | IX 100 / AS 2 | `starlink_gateways.json:403-408`；binding `:3-15` |
| `gw202` | Starlink Gateway - Boise, ID | 43.5811 | -116.29 | 0 | `198.18.2.1/32` | IX 101 / AS 3 | `starlink_gateways.json:147-152`；binding `:18-30` |
| `gw203` | Starlink Gateway - Porto Alegre, Brazil | -30.0346 | -51.2177 | 0 | `198.18.3.1/32` | IX 102 / AS 4 | `starlink_gateways.json:1387-1392`；binding `:33-45` |
| `gw204` | Starlink Gateway - Punta Arenas, Chile | -53.1638 | -70.9171 | 0 | `198.18.4.1/32` | IX 103 / AS 11 | `starlink_gateways.json:1435-1440`；binding `:48-60` |
| `gw205` | Starlink Gateway - Marsala, Italy | 37.7986 | 12.4357 | 0 | `198.18.5.1/32` | IX 104 / AS 12 | `starlink_gateways.json:1067-1072`；binding `:63-75` |
| `gw206` | Starlink Gateway - Isle of Man, UK | 54.13909 | -4.49728 | 0 | `198.18.6.1/32` | IX 105 / AS 2 | `starlink_gateways.json:803-808`；binding `:78-90` |
| `gw207` | Starlink Gateway - Marathon, ON | 48.754 | -86.379 | 0 | `198.18.7.1/32` | IX 106 / AS 3 | `starlink_gateways.json:1051-1056`；binding `:93-105` |
| `gw208` | Starlink Gateway - Saguenay, QC | 48.4279 | -71.0548 | 0 | `198.18.8.1/32` | IX 107 / AS 4 | `starlink_gateways.json:1523-1528`；binding `:108-120` |
| `gw209` | Starlink Gateway - Yamaguchi, Japan | 34.1861 | 131.4706 | 0 | `198.18.9.1/32` | IX 108 / AS 11 | `starlink_gateways.json:1995-2000`；binding `:123-135` |
| `gw210` | Starlink Gateway - Angeles, Philippines | 15.145 | 120.5887 | 0 | `198.18.10.1/32` | IX 109 / AS 12 | `starlink_gateways.json:43-48`；binding `:138-150` |
| `gw211` | Starlink Gateway - Murayjat, Oman | 23.588 | 58.545 | 0 | `198.18.11.1/32` | IX 110 / AS 2 | `starlink_gateways.json:1203-1208`；binding `:153-165` |
| `gw212` | Starlink Gateway - Foggia, Italy | 41.4621 | 15.5444 | 0 | `198.18.12.1/32` | IX 111 / AS 3 | `starlink_gateways.json:587-592`；binding `:168-180` |

表中 12 组有效坐标也由成功产物 `.../ground_manifest.json:701-760` 逐项确认。`station_type=starlink_ground_station` 有直接证据；v1 没有独立 `operator` 字段。把名称或目录来源推断为 `operator="Starlink"` 会增加 v1 未声明的语义，因此 operator 保持 `UNCERTAIN`。

### 4.3 GS 的 AS、Network、IX 和静态服务边界

当前成功 S4 中：

- 12 个 GS 都属于 AS 65000；`adapters/seedemu/integration.py:38` 是常量证据，`.../topology.json` 的 GS 条目及 `.../seed_output/docker-compose.yml:10839-10864` 的 `rnode_65000_gw201` 是产物证据。
- GS **没有加入任何 IX**，没有与外部 AS 建立静态 peering。Compose 中每个 GS 只有一个 `gwNN-bootstrap` 网络；例如 `gw201` 仅连接 `net_65000_gw201-bootstrap`，见 `.../seed_output/docker-compose.yml:10839-10864`。
- bootstrap 网络是 v1 后端启动资源，不是地面互联网语义，分类为 `BACKEND_RUNTIME`，不进入标准化场景草案。
- binding 中的 IX/upstream 值是未被当前成功场景物化的静态元数据，不能据此声称 GS 已加入 IX。其冲突在第 5、10 节单列。
- `198.18.1.1/32..198.18.12.1/32` 是 binding 明示的静态 service endpoint 数据；成功场景把它们作为后端 loopback reachability endpoint，而不是外部通告前缀。地址本身可作为 `EMULATOR_STATIC_OPTIONAL`，dummy/loopback 的实现属于 `BACKEND_RUNTIME`。
- 未发现 GS 的静态地面路由或静态应用 service。BIRD 在成功产物中只直连 bootstrap 网络，没有 eBGP；这不形成场景级路由输入。

## 5. 地面互联网与 peering

### 5.1 当前标准 S4 的有效静态事实

当前标准成功 S4 的有效地面互联网是**空集合**：

| 场景项 | 有效值 |
| --- | --- |
| 普通 SEED AS | 无 |
| 普通地面 Router | 无 |
| IX | 无 |
| 地面 Internet Network | 无 |
| GS–外部 AS 静态 peering | 无 |
| eBGP session | 无 |
| 对外通告地面前缀 | 无 |
| 仅供连通性测试的 host/network | 无 |

交叉证据：

1. 标准预设包含非空 `ut_gateway_policy_catalog`，见 `skyseed_v1/src/skyseed_v1/configuration/presets.py:21-28`。有效配置结果将 `include_mini_internet` 设为 false，见 `skyseed_v1/src/skyseed_v1/operations/preview.py:760-790,1043-1050`；结果字段在 `:1139-1157` 明确给出空的 mini-internet/IX/uplink 集合及 `gateway_service_loopback_only` 原因。这里只记录最终配置事实，不把这段控制流迁入 v2。
2. 成功 `.../topology.json:1256-1262` 记录 `mini_internet.enabled=false`、空 transit/stub/peerings。
3. 成功 `.../ground_manifest.json:649-698` 的 Compose footprint 对 IX、transit、stub 都是 0，并列出被移除的外部目标和 AS 2、3、4、11、12。
4. 成功 `.../run_manifest.json:138-170` 的 Compose audit 记录 `ix_network=0`、`mini_internet=0`、`management_static_ground=13`；13 项恰为一个 Satellite runtime management 网络和 12 个 GS bootstrap 网络，不是普通地面 Internet。
5. 成功 `.../seed_output/docker-compose.yml` 中不存在 `rnode_2_*`、`rnode_3_*`、`rnode_4_*`、`rnode_11_*`、`rnode_12_*`、`rnode_154_*`、`rnode_160_*`，也不存在 IX network。
6. `.../effective_profile.json:153` 的预期容器数 416 与 `configuration/resolver.py:17-21` 的 `348 + 2×28 + 12` 一致，没有普通地面 AS/Router 容器项。

因此，若目标被用户确认为重建**当前已成功运行并用于规模验证的 S4 静态 Emulator**，`ground_network_specifications` 和 `static_peerings` 应被显式表示为空，而不是从 binding 猜测一套实际未物化的互联网。是否采用这个解释仍是待确认的场景语义，本文不把它升级为无条件 S4 合同。

### 5.2 未物化的历史/能力元数据

仓库同时保留了另一组静态材料：

- 12 个 binding 条目分别含 raw IX `100..111` 和 upstream ASN `2,3,4,11,12`，见 `data/ground/s4_visualization_gateway_binding_12.json:3-180`。
- v1 的 mini-internet 能力目录含 transit AS 2、3、4、11、12，见 `adapters/seedemu/integration.py:42-124`。
- 该能力的 Starlink 子集选择过 stub AS 154、160，见 `topology/starlink.py:67-68`；测试目标 `as154-host-new=10.154.0.129`、`as160-host1=10.160.0.72` 见 `adapters/seedemu/integration.py:110-124`。
- `topology/starlink.py:1240-1361` 包含这类资源可能被加入的 v1 实现位置，但当前成功配置未启用它们；本文不总结该实现方式。

这些材料能证明“v1 曾保留一套可选地面 Internet 能力/绑定”，不能证明“当前标准 S4 静态上包含这些 AS、IX 和 peering”。除非用户明确要重建不同的历史变体，否则它们分类为 `UNCERTAIN` 或 `V1_IMPLEMENTATION_ARTIFACT`，不进入已确认输入集合。

为避免把“未物化”误写成“没有数据”，历史候选的可追溯静态清单如下：

| 候选项 | v1 中留下的值 | 当前处理 |
| --- | --- | --- |
| transit AS | 2、3、4、11、12 | `UNCERTAIN`；`integration.py:51-72` |
| stub AS | Starlink 子集为 154、160；完整能力表还列出其他 stub，但未被该子集选择 | 154/160 为历史候选，其余为 `V1_IMPLEMENTATION_ARTIFACT`；`integration.py:74-87`、`topology/starlink.py:67-68` |
| raw IX | 100..105 有显示名；GW binding 再引用 106..111 | `UNCERTAIN`；`integration.py:42-49`、binding `:3-180` |
| route-server peers | IX100: AS2/3/4；IX102: AS2/4；IX104: AS3/4；IX105: AS2/3 | `UNCERTAIN`；`integration.py:89-94` |
| 与所选 AS 有关的 private peerings | IX101: 2→12；IX102: 2/4→11/154、11→154；IX103: 3→160；IX104: 3/4→12；IX105: 3→11 | `UNCERTAIN`；从 `integration.py:96-109` 的静态表按所选 AS 取交集 |
| GS private peerings | 12 个 GS 按 binding 的 IX/upstream 与 AS65000 形成 provider/customer 候选 | `UNCERTAIN`；binding `:3-180` |
| Router/内部 Network 名称 | transit 候选使用 `as<asn>-r<ix>` 与 `net_<left_ix>_<right_ix>`；stub 候选使用 `as<asn>-router0` 与 `net0` | `V1_IMPLEMENTATION_ARTIFACT`；`integration.py:210-250`，不作为 v2 命名合同 |
| 显式 GS IX 地址 | `10.<raw-ix>.0.250`，逐项写在 binding | `UNCERTAIN`；只有选择历史变体才可能成为输入 |
| 显式测试 host | AS154 `as154-host-new=10.154.0.129`；AS160 `as160-host1=10.160.0.72` | 仅测试用途，`V1_IMPLEMENTATION_ARTIFACT`；`integration.py:110-124` |
| 通告前缀 | 当前 effective 基准无；历史能力没有在 S4 用户配置中给出一份稳定的显式通告前缀清单 | 当前为无；历史候选保持 `UNCERTAIN`，不得把通用 SEED 自动地址分配当作 S4 合同 |

上表中的箭头只表达 v1 静态表所标的 provider→customer 关系，不描述其实现调用链。它也不是一份获准的历史变体输入；用户若选择该变体，还需要先确认 raw/shifted IX 身份以及是否要固定通用 SEED 自动分配的前缀。

成功 `topology.json:35-41,1188-1248` 仍列出 12 个 `internet_uplink` inventory 项和偏移后的 IX `200..211`，但同一文件 `:1256-1262` 又明确 mini-internet disabled。此 inventory 不是实际 Compose 物化事实；raw binding 的 `100..111` 与 inventory 的 `200..211` 也不能被合并成一个确定的 IX ID 合同。

## 6. 静态节点软件、文件和服务

`skyseed_v1/src/skyseed_v1/topology/starlink.py:1240-1373` 和成功 Compose/Dockerfile 允许区分“场景数据”和“v1 后端内容”，但不构成 v2 实现模板。

| 观察项 | Satellite | GS | 分类与结论 | 证据 |
| --- | --- | --- | --- | --- |
| ASN/实体 ID/逻辑类别 | ASN 250、`stl-*`、`skyseed_role=satellite` | ASN 65000、`gw*`、`type=starlink_ground_station` | `EMULATOR_STATIC_REQUIRED` | `adapters/seedemu/integration.py:37-38`；`topology.json:2038-2085`；`models/ground.py:250-277` |
| 后端 SEED role | Router | Router | `V1_IMPLEMENTATION_ARTIFACT`；不能覆盖 v2 的 Satellite/GroundStation 类别 | Compose `:687-712,10839-10864` |
| `tcpdump` | 显式加入 | 显式加入 | 调试工具，`EMULATOR_STATIC_OPTIONAL`；不是创建静态实体的前提 | `topology/starlink.py:1240-1373` |
| BIRD/base image 工具 | 存在 | 存在 | SEED 编译/运行内容，`BACKEND_RUNTIME` | `.../seed_output/rnode_250_stl-940001/Dockerfile:1-17`、`.../rnode_65000_gw201/Dockerfile:1-17` |
| BIRD 配置文件 | 只有 runtime management direct | 只有 bootstrap direct | `BACKEND_RUNTIME`；没有场景级 eBGP | 对应生成目录中的 `2b0ae038330eccd43095538618caee7d` |
| start command/script | 启动 BIRD、设 sysctl、保持进程 | 同类内容 | `BACKEND_RUNTIME`/`V1_IMPLEMENTATION_ARTIFACT` | 对应生成目录中的 `082b96ec819c95ae773daebde675ef80` |
| environment | Compose `CONTAINER_NAME` 等 | 同类 | `V1_IMPLEMENTATION_ARTIFACT` | Compose `:687-712,10839-10864` |
| sysctl | forwarding、MPLS/socket 等 | 同类 | `BACKEND_RUNTIME`；不进入 Emulator 场景输入 | 生成 start 脚本 |
| runtime management/bootstrap Network | 一个 Satellite management 网络 | 每 GS 一个 bootstrap 网络 | `BACKEND_RUNTIME`；不表示静态互联网 | Compose audit `run_manifest.json:138-170` |
| service /32 | 无独立 S4 应用 service | binding 含 12 个 `/32` | 地址为 `EMULATOR_STATIC_OPTIONAL`；loopback 实现为 `BACKEND_RUNTIME` | binding `:3-180` |
| payload | 未发现 S4 专属 payload | 未发现 S4 专属 payload | 可留空；不从 v1 推断 | S4 入口、生成 Dockerfile/Compose |
| 任意场景文件/应用 service | 未发现 | 未发现 | 不属于静态必填输入 | 同上 |
| OSPF/iBGP | AS 250 被 mask | AS 65000 被 mask | “静态内部路由关闭”是有效场景事实；具体 mask API 是 v1 artifact | `topology/starlink.py:1363-1366` |

结论：v2 静态 Emulator 初始化不需要复制 v1 的 Dockerfile、BIRD、start script、environment、sysctl、management/bootstrap 网络、MPLS 或 `tcpdump`。Satellite 没有证据要求非空 payload；GS 没有证据要求静态应用 service。12 个 service `/32` 可以作为静态语义数据保留，但其后端实现可推迟。

## 7. 静态与动态边界分类表

| 配置项 | 分类 | 理由/证据 |
| --- | --- | --- |
| 场景标识 `S4` | `EMULATOR_STATIC_OPTIONAL` | 便于来源和验证，不应成为永久默认；`experiment.py:18` |
| Skyseed AS 250 | `EMULATOR_STATIC_REQUIRED` | 348 个 Satellite 的所有者 ASN；`integration.py:37` |
| Skyseed AS 65000 | `EMULATOR_STATIC_REQUIRED` | 12 个 GS 的唯一所有者 ASN；`integration.py:38` |
| 一个 shell、ID `S4` | `EMULATOR_STATIC_REQUIRED` | 标准入口和 preset |
| plane 数 6、每面 58 | `EMULATOR_STATIC_REQUIRED` | 规模和层级的生成输入 |
| Satellite 总数 348 | `EMULATOR_STATIC_REQUIRED`（派生校验值） | 不能与 `6×58` 独立变化 |
| altitude 560.0 km、inclination 97.6° | `EMULATOR_STATIC_REQUIRED` | shell 参数常量和实际目录一致 |
| RAAN 分布规则 | `EMULATOR_STATIC_REQUIRED` | 决定 6 个 Orbit 静态参数 |
| mean anomaly 分布规则 | `EMULATOR_STATIC_REQUIRED` | 决定轨内 Satellite 静态参数 |
| Orbit ID 规则 | `EMULATOR_STATIC_REQUIRED` | 稳定构造 6 个 Orbit；是否必须沿用原字符串待确认 |
| Satellite ID/显示名/NORAD 规则 | `EMULATOR_STATIC_REQUIRED` | 稳定构造 348 个实体；字符串兼容语义待确认 |
| reference epoch `2026-01-01T00:00:00Z` | `EMULATOR_STATIC_OPTIONAL` | 固定目录生成参考，不是运行时间范围；当前对象创建不要求它 |
| mean motion `15.045579289 rev/day` | `EMULATOR_STATIC_OPTIONAL`（派生校验值） | 可由 altitude 推导，不能成为独立冲突输入 |
| 动态位置、可见性、ISL、GS–Satellite link | `SIMULATOR_DYNAMIC` | 明确超出本轮边界 |
| GS 12 个 ID、名称、坐标、海拔、type | `EMULATOR_STATIC_REQUIRED` | binding、标准位置目录、manifest 一致 |
| GS operator | `UNCERTAIN` | v1 无独立字段；不得从名称猜测 |
| GS 数据来源/置信度/provenance | `EMULATOR_STATIC_OPTIONAL` | 有助可追溯，不影响实体创建 |
| GS service `/32` | `EMULATOR_STATIC_OPTIONAL` | 静态地址有证据，后端 loopback 可推迟 |
| GS service `/32` 的 dummy/loopback 物化 | `BACKEND_RUNTIME` | 容器网络实现 |
| 当前成功基准的普通地面 AS 集合为空 | `EMULATOR_STATIC_REQUIRED`（限 effective-baseline 解释） | 若选择当前成功基准，必须明确为空以避免误载旧能力 |
| 当前成功基准的 IX/Network/Router 集合为空 | `EMULATOR_STATIC_REQUIRED`（限 effective-baseline 解释） | Compose/topology effective 证据 |
| 当前成功基准的 static peering/eBGP 集合为空 | `EMULATOR_STATIC_REQUIRED`（限 effective-baseline 解释） | 成功产物没有 IX 或外部 AS |
| raw IX 100..111/upstream AS 2/3/4/11/12 | `UNCERTAIN` | binding 保留，但当前基准未物化 |
| 空 effective baseline 与历史 binding 二选一 | `UNCERTAIN` | 用户尚未确认要重建哪个 S4 静态语义 |
| inventory IX 200..211 | `V1_IMPLEMENTATION_ARTIFACT` | 与 raw ID 冲突且 effective mini-internet disabled |
| stub AS 154/160 和测试 host | `V1_IMPLEMENTATION_ARTIFACT` | 可选能力/测试数据，当前场景未物化 |
| Satellite management / GS bootstrap 网络 | `BACKEND_RUNTIME` | 只用于 v1 运行后端 |
| BIRD、Dockerfile、start command、environment、sysctl | `BACKEND_RUNTIME` | 不是场景静态实体合同 |
| `tcpdump` | `EMULATOR_STATIC_OPTIONAL` | 调试包，不是基本场景要求 |
| payload/应用 service | `EMULATOR_STATIC_OPTIONAL` | 当前证据支持为空，不得凭空添加 |
| OSPF/iBGP 关闭 | `EMULATOR_STATIC_REQUIRED` 的路由意图 | 当前两个 Skyseed AS 没有静态内部路由；v1 mask 手段不是合同 |
| 时间范围、slice/horizon interval | `SIMULATOR_DYNAMIC` | 明确排除 |
| Zone、G/Z 路由 | `SIMULATOR_DYNAMIC` | 明确排除本轮输入 |
| UT | `UNCERTAIN` | 明确在本轮范围外；本文不替后续切片决定其静态/动态归属 |
| qdisc、BPF maps、generation、preload、slot、MPLS | `BACKEND_RUNTIME` 或 `V1_IMPLEMENTATION_ARTIFACT` | 不属于静态 Emulator |
| runtime resource inventory | `V1_IMPLEMENTATION_ARTIFACT` | 不能代替实际 Compose/effective 配置 |

## 8. 标准化静态场景输入草案

这是与 v1 文件格式和类名无关的数据需求草案，不决定 Python API、类、loader 或最终序列化格式。`R` 表示必填，`O` 表示可选，`D` 表示由其他字段派生且不得独立配置，`U` 表示有证据但语义尚待用户确认。含 `U` 的草案不是可直接实现的最终输入。

```text
StaticEmulatorScenario
├── scenario_id                                      O  "S4"，仅作来源/验证标识
├── source_provenance                                O  v1 commit、证据目录
├── skyseed_autonomous_systems                       R
│   ├── asn=250                                      R  entity_kinds={satellite}
│   │   └── internal_routing_mode=disabled           R
│   └── asn=65000                                    R  entity_kinds={ground_station}
│       └── internal_routing_mode=disabled           R
├── shell_specifications                             R  恰好 1 项
│   └── shell_id="S4"                               R
│       ├── owner_asn=250                            R
│       ├── plane_count=6                            R
│       ├── satellites_per_plane=58                  R
│       ├── satellite_count=348                      D  plane_count × satellites_per_plane
│       └── orbit_generation_specifications          R
│           ├── altitude_km=560.0                    R
│           ├── inclination_deg=97.6                 R
│           ├── orbital_reference_epoch_utc          O  2026-01-01T00:00:00Z
│           ├── mean_motion_rev_per_day               D  15.045579289，仅用于校验
│           ├── raan_start_deg=0.0                   R
│           ├── raan_step_deg=60.0                   D  360 / plane_count
│           ├── mean_anomaly_step_deg=360/58         D
│           ├── alternating_plane_phase_deg=180/58   D
│           ├── orbit_identity_rule                  R  1..6 -> stable Orbit ID
│           └── satellite_identity_rule              R  plane/satellite -> ID、显示名、NORAD
├── ground_station_specifications                    R  恰好 12 项
│   └── each ground station
│       ├── station_id                               R  gw201..gw212
│       ├── display_name                             R
│       ├── latitude_deg / longitude_deg             R
│       ├── elevation_m                              R
│       ├── station_type                             R  starlink_ground_station
│       ├── owner_asn=65000                          R
│       ├── operator                                 O  当前语义 UNCERTAIN
│       ├── service_addresses                        O  对应 198.18.1.1/32..198.18.12.1/32
│       └── source_provenance                        O
├── ground_network_specifications                    U
│   ├── effective_successful_baseline                证据值 []
│   └── historical_capability_candidate              AS 2/3/4/11/12/154/160、raw IX 100..111；未物化
└── static_peerings                                  U
    ├── effective_successful_baseline                证据值 []
    └── historical_capability_candidate              mini-Internet peerings 和 12 个 GS/upstream 关系；未物化
```

字段证据映射：

| 草案字段 | v1 证据 |
| --- | --- |
| `scenario_id`、单 shell | `examples/s4_operational_clean/experiment.py:14-25` |
| AS 250/65000 | `adapters/seedemu/integration.py:37-38`；Compose 标签 |
| shell 规模 | `configuration/presets.py:8-10`、`configuration/models.py:10-19` |
| altitude/inclination | `topology/shell.py:43-49` |
| fixed reference epoch/derived mean motion | `topology/shell.py:181-223`；orbit JSON S4 记录 |
| RAAN/anomaly/身份规则 | `topology/shell.py:181-223,265-303`；orbit JSON 首末记录 |
| 12 个 GS | `configuration/presets.py:21-39`、binding `:1-182`、标准位置目录对应行 |
| GS type/source | `models/ground.py:235-279` |
| GS service address | binding `:3-180` |
| effective 空地面网络/peering | 成功 `topology.json:1256-1262`、`ground_manifest.json:649-698`、`run_manifest.json:138-170`、Compose |
| 未物化历史候选 | binding `:3-180`、`integration.py:42-124`；与 effective 证据冲突，不能直接采用 |

草案有意不包含：v1 dict/JSON 布局、v1 类名、profile、Runner/Planner/Compiler 参数、动态时间参数、UT/Zone、网络 namespace、Compose service、bootstrap/management 网络、BPF/MPLS/qdisc。地面网络的两个证据分支只是待裁决事实，不表示草案同时加载两者。若用户选择带 mini-internet 的历史变体，应形成一个**显式命名的不同场景输入**，不能静默改变 effective 成功基准，也不能把 S4 设为 Emulator 永久默认场景。

## 9. 证据索引

| 主题 | 路径、符号/键/数据行 | 支持的事实 |
| --- | --- | --- |
| 标准 S4 入口 | `skyseed_v1/examples/s4_operational_clean/experiment.py:14-25`，`EXPERIMENT` | 显式选择 S4 和 S4 ground preset |
| 完整规模声明 | `skyseed_v1/examples/s4_operational_clean/README_EN.md:6-22` | 348、28 UT、12 GW、14 zone；348 不允许静默 cap；本轮只取 Satellite/GW |
| shell 配置 | `skyseed_v1/src/skyseed_v1/configuration/presets.py:6-10`，`SHELLS["S4"]` | 6 plane、58 Satellite/plane |
| 总数派生 | `skyseed_v1/src/skyseed_v1/configuration/models.py:10-19`，`ShellConfig.satellite_count` | 348 是乘积 |
| 轨道参数 | `skyseed_v1/src/skyseed_v1/topology/shell.py:43-49`，`PLANNED_SHELL_PRESETS` | 560.0 km、97.6° |
| 轨道生成规则 | `skyseed_v1/src/skyseed_v1/topology/shell.py:181-223` | RAAN、phase、anomaly、NORAD、显示名 |
| Orbit/节点 ID | `skyseed_v1/src/skyseed_v1/topology/shell.py:52-54,265-303` | `stl-*`、`shell-plane-*` |
| 轨道目录 | `skyseed_v1/data/orbits/planned_shell_orbit_data_s1_s5.json:1-14,58335-63553` | 348 条 S4 实际记录及首末值 |
| S4 ground preset | `skyseed_v1/src/skyseed_v1/configuration/presets.py:21-39`，`s4_rich_ground_network`；`configuration/resolver.py:35-48` | binding、12 个选择 ID、派生上限 12 |
| GS binding | `skyseed_v1/data/ground/s4_visualization_gateway_binding_12.json:1-182` | 12 个 ID、标准目录 ID、service `/32`、raw IX/upstream |
| 标准 GS 目录 | `skyseed_v1/data/ground/starlink_gateways.json:43-48,147-152,403-408,587-592,803-808,1051-1056,1067-1072,1203-1208,1387-1392,1435-1440,1523-1528,1995-2000` | 12 个显示名、经纬度、海拔 |
| GS 标准化 | `skyseed_v1/src/skyseed_v1/models/ground.py:235-279,319-359` | type、source、单位转换及所需字段 |
| AS 常量 | `skyseed_v1/src/skyseed_v1/adapters/seedemu/integration.py:37-38` | Satellite ASN 250、GS ASN 65000 |
| mini-internet 有效关闭 | `skyseed_v1/src/skyseed_v1/operations/preview.py:760-790,1043-1050,1139-1157` | 当前标准配置的最终空集合和原因 |
| 可选地面能力 | `skyseed_v1/src/skyseed_v1/adapters/seedemu/integration.py:42-124,210-250`；`topology/starlink.py:67-68,1240-1361` | 未物化 transit/stub/IX/peering/Router/test host 能力，不是当前事实 |
| 节点软件/路由意图 | `skyseed_v1/src/skyseed_v1/topology/starlink.py:1240-1373` | tcpdump、AS 创建、内部路由关闭；实现细节不迁移 |
| 成功验证声明 | `skyseed_v1/docs/operations/validation_status_EN.md:38-61` | 当前成功 S4 为 348 Satellite、12 GW |
| shell 产物 | `.../rich_s4_operational_clean_bpf-20260713T033419Z/shell_manifest.json:20342-20358,20785-21127` | 348、6×58、RAAN；550 是 bucket |
| ground 产物 | `.../ground_manifest.json:649-760,903-969` | 有效无 IX/mini-internet、12 个 GS、坐标 |
| topology 产物 | `.../topology.json:35-41,1188-1262,2029-2035` | inventory 冲突、effective disabled、数量汇总 |
| Compose audit | `.../run_manifest.json:138-170` | IX/mini-internet 均 0、13 个后端静态网络 |
| 有效容器规模 | `.../effective_profile.json:153`；`configuration/resolver.py:17-21` | 416 公式不含普通地面互联网节点 |
| 真实服务 | `.../seed_output/docker-compose.yml:687-712,10839-10864` | Satellite/GS ASN、类型、实际接口；无 IX |
| 真实镜像内容 | `.../seed_output/rnode_250_stl-940001/Dockerfile:1-17`；`.../rnode_65000_gw201/Dockerfile:1-17` | 通用 SEED/backend 文件，不是场景合同 |

上表中 `.../` 均指 `skyseed_v1/examples/s4_operational_clean/output/rich_s4_operational_clean_bpf-20260713T033419Z/`。

## 10. 冲突和不确定项

1. **当前有效地面网络与 binding 元数据冲突。** binding 写有 IX 100..111 和 upstream AS 2/3/4/11/12；当前成功 effective 配置和 Compose 却是零 IX、零普通地面 AS、零 eBGP。真实运行来源更接近 effective profile、manifest 和 Compose，因此“当前成功基准”的证据值是空集合；标准化草案仍把场景选择标成 `UNCERTAIN`，是否另行重建历史 mini-internet 变体必须由用户决定。
2. **IX ID 自身冲突。** raw binding 是 100..111；`topology.json` inventory 是 200..211。后者来自 v1 运行偏移语义，且没有物化，不能选作新合同 ID。
3. **altitude 表面冲突已消解。** preset、轨道目录和轨道参数是 560.0 km；manifest 的 550.0 是 25 km cluster bucket，不是 altitude override。
4. **产物 commit provenance 不完整。** 成功产物没有内嵌 commit。它生成于最后修改 v1 的 `06b19e...` 之前约一小时；该 commit 未改变本调查使用的静态入口、轨道目录或 GS binding，当前 v1 tree 的静态事实和产物一致，但不能声称产物自己记录了 `06b19e...`。
5. **operator 缺失。** `station_type=starlink_ground_station` 有证据，`operator="Starlink"` 没有独立字段证据。
6. **身份兼容语义未决。** v1 同时有 `stl-940001` 节点 ID、`STARLINK-S4-P01-S01` 显示名和 NORAD 940001。v2 是否必须逐字符串保留节点/Orbit ID，还是只需保持确定性一一映射，需要用户确认。
7. **service `/32` 的层次未决。** 地址是静态数据，但当前用途是后端 loopback service endpoint，而不是地面前缀通告。是否在第一轮静态重建中保存它，尚需确认。
8. **未发现另一个活跃的不同规模 S4 场景。** 全仓 S4 命名检查中，标准 `s4_operational_clean` 明确是完整 348 颗；“shortened full-S4 replay”是审计范围而不是另一份静态 shell 定义。测试和文档也反对用 `max_satellites` 静默缩小 S4。历史文档、工具 CLI 示例和动态审计不应被当作静态场景入口。
9. **轨道 reference epoch 的合同归属未决。** `2026-01-01T00:00:00Z` 是固定目录生成参考，不是 S4 实验时间范围；当前 v2 静态对象创建不需要它，但若后续 Simulator 要从 mean anomaly 重建同一物理状态，可能需要保留为可选场景 provenance/交接数据。

## 11. 下一轮实现前需要用户确认的问题

1. 规模验证输入是否以当前成功基准为准，即普通地面 AS、IX、Network 和 static peering **全部为空**？还是要另行定义一个显式命名、包含 binding 中旧 IX/upstream 的历史 mini-internet 变体？
2. v2 是否必须保留 `stl-940001..stl-940348` 和 `shell-plane-001..006` 的原字符串身份，还是允许 v2-native ID，只要求与 v1 NORAD/plane/satellite 索引保持稳定一一映射？
3. GS `operator` 是否应由用户显式指定为 `Starlink`，还是保持未设置，只使用有证据的 `station_type=starlink_ground_station`？
4. 12 个 `198.18.<1..12>.1/32` service address 是否纳入第一轮静态场景输入，还是连同其 loopback 服务语义一起推迟到 Simulator/Backend 阶段？
5. 固定轨道目录参考 epoch `2026-01-01T00:00:00Z` 是否作为可选 provenance/未来 Simulator 交接字段保留，还是完全不进入本轮标准化输入？

在这些问题得到明确答复前，不开始实现，不创建 loader，不实现批量生成或 GS 导入，也不把 S4 设为 Emulator 默认场景。
