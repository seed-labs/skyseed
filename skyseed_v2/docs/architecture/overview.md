# Initial Architecture Overview

## 1. Status

本文档记录 Skyseed v2 当前已经确认的初步架构方向。

它不是完整设计，也不定义最终类、字段或 API。具体对象和合同将在相应垂直切片开始时逐步设计和确认。

Detailed conceptual class diagrams with responsibilities, key attributes and
methods are maintained in
[conceptual-class-diagrams.md](./conceptual-class-diagrams.md).

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
- 向 Simulator 提供其真正需要的只读实体信息。

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
- 与实际运行节点建立对应关系所需的稳定引用。

Simulator 不应获得或依赖与动态计算无关的容器构建细节。

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

- 基础节点与 SEED Node/Router 的关系；
- Emulator 内部对象的最终组成；
- Emulator–Simulator 合同的具体类型；
- Topology 子系统的最终对象结构；
- Slice 的完整数据合同；
- Semantic Plan 和 Backend Plan 的具体格式；
- Compiler 与 Runner 的精确接口；
- UT 子系统；
- 有限运行和流式运行；
- 真实后端与 v1 优化能力的吸收方式。
