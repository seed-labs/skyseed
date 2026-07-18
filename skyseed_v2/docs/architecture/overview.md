# Initial Architecture Overview

## 1. Status

本文档记录 Skyseed v2 当前已经确认的初步架构方向。

它不是完整设计，也不定义最终类、字段或 API。具体对象和合同将在相应垂直切片开始时逐步设计和确认。

Detailed conceptual class diagrams with responsibilities, key attributes and
methods are maintained in
[conceptual-class-diagrams.md](./conceptual-class-diagrams.md).

Public terminology and layer-qualified names are normative in
[terminology.md](./terminology.md); repository-level approval and synchronization
rules are in the root `AGENTS.md`.

## 2. System division

系统分为两个主要部分：

- Emulator
- Simulator

### Emulator

Emulator 基于 SEED 的基础能力，负责定义和物化预先确定的静态实体及其运行环境。

当前确认的职责包括：

- 创建卫星和地面站等静态实体；
- 保存轨道、位置和稳定标识等静态属性；
- 配置容器和 SEED 相关运行信息；
- 构建地面互联网；
- 配置地面站与地面互联网之间的静态关系；
- 通过不可变 `SimulationEntityCatalog` 向 Simulator 提供其真正需要的只读
  静态实体信息。

Emulator 不负责：

- 时间演化；
- 卫星可见性；
- 卫星网络内部连接；
- 动态路由；
- 拓扑切换；
- 运行时更新规划。

UT 暂时不进入当前主体 Emulator 切片，后续作为相对独立的部分设计。

### Simulator

Simulator 负责所有与时间和卫星网络动态状态有关的部分。

当前初步链路为：

1. 接收 Emulator 提供的只读实体信息；
2. 定义 Zone 规则；
3. 定义模拟时间和切片规则；
4. 生成连接状态；
5. 生成路由或转发状态；
6. 形成拓扑切片；
7. 比较相邻切片；
8. 生成后端无关的语义变化计划；
9. 根据所选后端生成后端执行计划；
10. 编译为可执行内容；
11. 由 Runner 执行；
12. 记录观察与运行证据。

## 3. Emulator–Simulator boundary

Emulator 和 Simulator 之间首先是领域对象或接口合同，而不是某一种文件格式。

Simulator 只应获得其计算动态状态所需的只读信息，例如：

- 卫星稳定标识；
- shell 和 orbit 归属；
- 轨道参数；
- 地面站稳定标识；
- 地面站位置；
- 与运行后端无关的统一逻辑节点身份。

Simulator 不应获得或依赖 SEED Registry、容器、namespace、Compose service
或其他执行目标细节。

SatelliteDescriptor 与 GroundStationDescriptor 都包含定义在公共
`contracts.identity` 模块中的不可变 `SimulationNodeId`。其值由 `asn`、
`entity_kind` 和 `entity_id` 组成，只标识 Skyseed/Simulator 中的逻辑节点。
Satellite 与 GroundStation 实体本身不依赖合同层，也不保存该值；各 Descriptor
Projector 从唯一实体拥有的静态标量构造它。同一 ASN 内即使两类实体使用相同
文本 ID，也会因为 `entity_kind` 不同而保持唯一。

当前合同要求 ASN 是正整数，`entity_kind` 只能是 `satellite` 或
`ground-station`，`entity_id` 使用公共标识符规则校验。该值对象冻结、可哈希，
稳定字符串形式为 `asn/entity_kind/entity_id`。

`SimulationNodeId` 不等于 SEED Registry identity、容器 identity 或 execution
target。Simulator 的 Topology、Connection、Route 和 Semantic Planning 阶段只
使用逻辑身份。当 Backend Planner、Execution Compiler 或 Runner 需要把逻辑
节点转换为实际 SEED Node、容器、namespace 或内核资源时，再根据当时的执行
合同设计运行目标引用与解析机制；这属于尚未进入的后续设计范围，而非当前
技术债。

三种身份信息保持明确分层：

- `SimulationNodeId = asn/entity_kind/entity_id`，对 Simulator 可见；
- SEED Registry identity = scope、`satnode|rnode|brdnode`、node name，只供
  SEED integration 生命周期使用；
- InternetMap metadata = `type`、`role` 和相关 labels，只供运行环境与可视化
  使用。

Compiler 从 SEED 集成对象产生运行 metadata，不通过 Descriptor 获取它。

`SimulationEntityCatalog` 是当前已经实现的 Emulator 输出边界。独立的
`SimulationEntityCatalogBuilder` 接收一个或多个 AS，只遍历其中的
`SkyseedAutonomousSystem`，并委托现有 Satellite/GroundStation Projector 生成
Descriptor。Catalog 对 `SimulationNodeId` 做全局唯一性检查，以确定性顺序保存
不可修改的 Descriptor tuple，并提供按完整逻辑 ID 或无歧义实体 ID 的查询。
它不读取 Registry、Compiler output 或 Compose，也不定义持久化格式。
`SkyseedAutonomousSystem` 不创建 Descriptor、不持有 Projector，也不依赖合同层；
多 AS 投影汇总只有这一条 application 路径。

同进程对象传递、文件序列化或跨进程传输只是这一合同的不同适配方式，不属于领域边界本身。

## 4. Topology subsystem

Topology 是拓扑子系统的统一入口或协调对象，而不是实现所有拓扑逻辑的巨型类。

与以下能力相关的数据和方法，应由真正拥有对应数据和职责的对象实现：

- 模拟时间；
- Zone 查询与选择；
- 可见性计算；
- 连接选择；
- ConnectionSlice；
- 路由计算；
- RouteSlice；
- TopologySlice；
- Slice 保存与窗口管理；
- 相邻切片差异计算。

生成完成的 Slice 原则上应保持不可变。

## 5. Planning subsystem

Planning 分为至少两个连续层次。

### Semantic planning

后端无关，只描述网络语义上发生的变化，例如：

- 连接建立或删除；
- 链路参数变化；
- 节点转发出口变化；
- FEC 或等价转发状态变化。

### Backend planning

根据所选后端，将语义变化转换为后端操作。

未来可能存在不同实现，例如：

- BPF backend planner；
- Linux MPLS backend planner；
- IP routing backend planner。

后端 Planner 不应重新计算拓扑或形成第二条语义规划路径。

## 6. Compiler and Runner

Compiler 将已经确定的后端计划转换为可执行内容、配置和执行约束。

Compiler 不应重新作出拓扑或路由决策。

Runner 不负责网络业务判断或后端规划，只负责按照已经编译的执行内容和依赖关系执行操作，并产生结构化运行事件。

Runner 可以执行通用的执行合同检查，但不得自行补算计划或选择替代路径。

## 7. Observation

Observation 负责收集执行过程、操作结果、时间和错误等信息。

记录过程应尽量不阻塞主要执行流程，但关键执行事件必须可靠产生并可审计。

## 8. Deferred design

以下内容尚未确定，将在相关垂直切片中继续设计：

- Satellite 与 GroundStation 之外 Emulator 对象的最终组成和只读合同；
- Topology 子系统的最终对象结构；
- Slice 的完整数据合同；
- Semantic Plan 和 Backend Plan 的具体格式；
- Compiler 与 Runner 的精确接口；
- Backend Planner、Execution Compiler 或 Runner 如何把 `SimulationNodeId`
  解析为实际 SEED Node、容器、namespace 或内核资源；
- UT 子系统；
- 有限运行和流式运行；
- 真实后端与 v1 优化能力的吸收方式。

## 9. First implemented vertical slice

孤立 Satellite 静态 Emulator 已确定并实现以下边界：

- `Satellite` 是唯一的 Skyseed 卫星对象，直接继承 SEED `Node`，并以独有
  `satnode`/`Satellite` 身份参加 Registry、configure 和 Compiler 生命周期；
- `SatelliteDescriptor` 是不可变的 Simulator-facing 值对象；
- `SatelliteDescriptorProjector` 从唯一 Satellite 读取批准的静态值，并从
  稳定标量构造逻辑 `SimulationNodeId`；Simulator 不接收完整 Satellite/Node；
- `Shell -> Orbit -> Satellite` 是领域所有权层级；
- Satellite 容器使用显式 `network_mode: none`，除 loopback 外没有接口；
- Satellite 编译特例集中在单一内部边界，私有 SEED service accumulator
  仅通过一个 fail-closed 封装点访问；
- Compose metadata 先按完整 key 唯一化，再显式写入 Satellite 身份；
- SEED runtime InternetMap 通过 labels 发现 Satellite，并将 Docker 的
  `none` 伪网络表示归一化为空图边集合。

具体合同见 `docs/design/isolated-satellite-emulator.md`，真实验证证据见
`docs/evidence/isolated-satellite-validation.md`。

## 10. Implemented static GroundStation direction

GroundStation 的单对象模型已实现并通过真实运行验证。用户只创建和管理一个
GroundStation；不存在长期节点 adapter、`GroundStationNode` 或二者间 binding。

该唯一对象直接继承 SEED Router，同时承载地面站静态语义与标准节点能力。
静态语义包括稳定 ID、位置与可选海拔、运营商、类型及受限 service config。
节点能力包括 Registry/configure
生命周期、容器配置、加入地面 Network/IX、地面 forwarding、显式启用的路由
协议与 Service、metadata，以及未来卫星侧动态接口的执行入口。

GroundStation 不负责可见性、连接选择、Zone 服务卫星选择、动态路径、拓扑
切片或后端更新规划，这些仍属于 Simulator。

Simulator 只接收不可变 `GroundStationDescriptor`。Descriptor 提供位置、
分类、operator 与逻辑 `SimulationNodeId`，不暴露完整 Router、
Registry、路由守护进程配置、软件/文件/命令、Docker/Compose 对象、可修改接口
或 Emulator 生命周期方法。

本轮证明直接继承 SEED Router 足以复用标准 `rnode`、IX border-router、
Routing/eBGP、Docker Compiler 与 InternetMap 路径，且无需 GS 专用 Compiler
dispatch。该结论仍是 evolving 边界，不会自动决定未来动态接口、Service 或
复杂路由策略。

具体合同见 `docs/design/static-ground-station-emulator.md`，真实验证证据见
`docs/evidence/static-ground-station-validation.md`。

## 11. Implemented structural convergence

`SkyseedAutonomousSystem` 是唯一公开的 Skyseed AS 扩展。一个实例可以同时拥有
Shell/Orbit/Satellite、GroundStation 以及继承的普通 Network、Host 和 Router；
也可以按卫星-only、GS-only 或混合方式使用。多个实例各自维护独立所有权和
稳定 ID 索引。旧的按实体类型拆分 AS 已被删除且没有兼容别名。

`SkyseedDockerCompiler` 是唯一公开的 Skyseed Docker Compiler。它沿用 SEED
Docker 编译流程，只把 `satnode` 委托给 `_SatelliteCompilerBoundary`；
GroundStation 仍走标准 Router 路径。上游缺少 custom-node dispatch hook 导致的
私有 service accumulator 访问仍限制在一个 fail-closed 封装点。

跨领域使用的标识符验证位于 `domain.validation`，SEED/Compose metadata 命名
位于 `seed.metadata`。因此 Satellite 与 GroundStation 领域模块不再互相
import，也不依赖 `contracts` 或 Compiler metadata 细节。`satnode`、`rnode`、
`brdnode` 以及 `Satellite`/`GroundStation` 运行 metadata 仍只属于 SEED 集成层，
不进入 Descriptor。

组合场景的测试、容器、路由、InternetMap 与清理证据见
`docs/evidence/skyseed-structure-convergence-validation.md`。

## 12. Implemented SimulationEntityCatalog boundary

静态 Emulator 综合输出已由以下实现收口：

- `SimulationEntityCatalog`：冻结的 Descriptor 快照，只包含
  `SatelliteDescriptor`、`GroundStationDescriptor` 和只读逻辑 ID 索引；
- `SimulationEntityCatalogBuilder`：支持 mixed、Satellite-only、GS-only 和
  多个 Skyseed AS，忽略普通 SEED AS；
- Catalog 构造对完整 `SimulationNodeId` 重复 fail-closed，不静默覆盖；
- Catalog 顺序仅由 `SimulationNodeId.value` 决定，不受 AS 或实体遍历顺序影响；
- 构建后的 AS 变化不影响既有 Catalog；
- 正式 `examples/complete_static_emulator/` 实验同时生成 Catalog、编译真实混合场景并
  启用 InternetMap。

自动化 Emulator 集成验收与人工浏览器视觉验收均已完成，正式运行环境也已按
项目范围清理。Catalog 当前状态为 **implemented**；**Static Emulator core
excluding UT: COMPLETE**。这不会启动 Simulator、UT、动态链路或运行资源解析。

## 13. Compose materialization integration boundary

大规模静态场景的部署/集成路径现在明确分为三层：

```text
Emulator:                     构建静态实体和场景
    -> SkyseedDockerCompiler: 生成唯一 Compose output
    -> ComposeProjectLauncher: 通过 Docker Compose v2 物化完整 project
```

`ComposeProjectLauncher` 位于 `skyseed_v2.materialization`，不属于 Emulator
领域对象、Descriptor、Catalog、Simulator 或动态 Topology/Planner。它接收已生成
的 Compose 文件、project name、可配置 `parallel_limit`，以及调用者显式给出的
长期运行/成功退出 helper 服务集合。默认只发出一次完整项目的
`docker compose --parallel N ... up -d`，随后 fail-closed 核验最终完整清单。它
不调用 Compiler、不修改或重写 Compose、不缩减服务、不自动调参，也不实现
重试/恢复框架；Compose v2 缺失或失败时不会回退到 v1。

Compose 1.27.4 在 365-service 一次性大扇出中发生客户端阻塞；Compose v2.40.2
则用同一文件、同一 project 和 `parallel_limit=16` 完成了单次完整物化。固定
分批能力仍以显式 `strategy=batched` 保留为兼容/诊断工具，绝不是自动 fallback
或第二条默认路径。未来 Runner 是否调用、封装或替换它仍未决定，本轮没有开始
动态 Runner 或 Simulator。详细合同与实验证据见
`docs/design/compose-materialization.md` 和
`docs/evidence/s4-scale-compose-v2-single-materialization-validation.md`。
