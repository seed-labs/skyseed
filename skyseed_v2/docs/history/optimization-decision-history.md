# 已采用优化与宿主机配置的决策历史

> Historical record, retained under its original dates, parameters and source revisions.
> Commands and paths below describe that work; use the current guides for new operations.
> This record does not validate phase 4b-2.

Phase 4b-1 retention note: normal generated output directories and presentation
pages were removed. Links to retained measurements below now target their selected
external evidence. Original source paths, timings and historical limits are unchanged.
See [cleanup/retention manifest](/home/lionheart/workspace/skyseed-local/experiments/phase4b-1-20260917T144814Z/cleanup-retention-manifest.json).

Non-authoritative decision history; current contracts live in accepted-design.md.

本记录保存已采用机制、可追溯源码和现有证据的边界，不建立新的架构权威、
优化方案、配置接口或运行组件。人类入口是 [中文机制说明](../architecture/mechanisms.md)；
当前合同以 [accepted design](../development/README.md) 为准，阶段状态只见
[CURRENT](../CURRENT.md)。各轮新增测量及其范围在对应条目单独标明。

2026-09-10 累计 Producer、多 Shell、Runner 和 Tools 里程碑已经用户与 Work 接受。
当轮关闭只处理保留位置、文档及 Git 提交，不重跑实验。下面的阶段性“当时 pending”
描述是决策时点背景；当前接受状态以 CURRENT 为准。外部历史实验 raw bundles、
失败运行、完整旧 Tool portals、临时诊断及重复 source snapshots 已删除，选定的
测量/核验摘要已汇入正常五 Shell output 的 qualification 目录。下面提及的原始
路径仅说明当时出处，不表示文件仍存在；可点击链接只指向保留摘要。Git 中既有的
Runner milestone archive 及其 75 个索引成员继续保留。

## 证据读法与检查点

- **当前源码事实**：早期 R/D/H 章节的实现检查点为 `6e8652a`。源码链接指向仓库文件，
  符号名用于定位；将来代码变化时，可用该 Git 检查点恢复本文相应历史章节所述版本；后续章节以其标注轮次为准。
- **直接历史证据**：Git 中的旧源码、旧文档和已有里程碑结果。某机制在一个
  检查点存在，不等于该提交日期就是它的精确批准日期。
- **Work 核验结论**：宿主机当前事实来自用户在本次文档任务中提供的 Work
  调查结果；本次没有重新查询 Docker、bus、sysctl 或挂载状态。
- **理由与推断**：机制减少哪类工作可由源码解释；没有决策原文的选值理由、
  隔离实验和精确收益则明确保留缺口，不把推断写成历史批准事实。

| Git 检查点 | 本文采用的证据范围 |
|---|---|
| `19a4f79` | 旧 `simulator/models/geometric_connection.py` 已有 ECEF 网格与保守查询函数。 |
| `b00fc77` | 旧 `models/hop_count_route.py` 的按目标 reverse BFS，以及 `tools/_detached_inspection.py` 的 detached worker。 |
| `fa280e0` | build-context 分组、canonical 参数选值、旧 system-overview 的宿主机控制面记录。 |
| `1b3d14c` | 当前 Simulator/Planner 归属、grouped Route engine、indexed sparse GS solver、紧凑证据及当时的离线测量。 |
| `6e8652a` | 有限 Runner、artifact-v3、当前私有执行设施和 Runner 联合优化里程碑。 |

历史文档可直接从 Git 读取，例如：

```bash
git show fa280e0:skyseed_v2/docs/human-readable/system-overview.md
git show 1b3d14c:skyseed_v2/docs/CURRENT.md
```

前者的原始第 76–143 行保留 D-Bus/inotify/cgroup 调整背景；它在 `1b3d14c`
的文档重写中移除。本文恢复相关事实，不恢复旧领域结构或旧运行流程。

## 计算机制

<a id="c1"></a>
### C1：保守 GS/Zone 空间索引

**Owner / scope。** `GeometricPhysicalLinkModel` 的私有几何实现，只过滤
物理候选，不作 GS 分配、Zone 服务选择或 Route 规划。
源码（历史源路径：`../../src/skyseed_v2/simulator/models/_geometric_physical.py`）：
`_query_spatial_entries`、`_maximum_eligible_slant_distance_m`、
`_spatial_radius_bounds`。

**机制与配置。** 静态 GS/Zone ECEF 坐标按 2,000,000 m 边长进入 uniform-grid
cells；查询半径加 1 m 容差。上界结合卫星半径、地面半径范围、最低仰角及
WGS84 local-up 相对径向的偏差，先筛格子和距离，再调用精确几何资格判断。
非正修正仰角等情形使用更宽的保守范围。索引保持原对象顺序，不能漏掉合格
候选；它不是 KD-tree，也不近似替代最终 eligibility。

**历史、理由与限制。** `19a4f79` 的
`skyseed_v2/src/skyseed_v2/simulator/models/geometric_connection.py` 已有同名
查询函数；`1b3d14c` 后归当前物理 Model。减少需精算的候选是源码可解释的
收益机制；上界过宽时仍可能查询大量对象。没有从现有记录恢复单独加速数字。

<a id="c2"></a>
### C2：按终点复用 reverse BFS 与所需路径分支

**Owner / scope。** Planner 的 Route Model 使用
`_routing_core.py`（历史源路径：`../../src/skyseed_v2/simulator/_routing_core.py`） 和
`_route_computation.py`（历史源路径：`../../src/skyseed_v2/simulator/_route_computation.py`），
不改变 RouteDemand 或 Connection 的领域归属。

**机制。** `_GroupedRouteEngine.compute` 按 `(ground_station_target, terminal)`
分组。`_reverse_distances` 用 FIFO 队列从终点求最少跳数；GS 终点和 Zone
服务 Satellite 分别遵守各自中转限制。搜索收齐所需源后可结束，随后
`_materialize_required_branch` 仅展开请求路径需要的分支，并共享已求出的
distance/next-hop 选择。按规范顺序选择 next hop，保留明确不可达结果。

**历史、理由与限制。** `b00fc77` 的
`skyseed_v2/src/skyseed_v2/simulator/models/hop_count_route.py` 已按目标做
reverse BFS；当前 demand-bounded engine 存在于 `1b3d14c`，在 `6e8652a`
沿用。复用的是同一图上同终点的搜索，不是跨任意拓扑变化缓存结果。该搜索
以跳数为代价，不是 Dijkstra；没有隔离的当前性能对照。

<a id="c3"></a>
### C3：可复用、有界的 Route 进程池

**Owner / scope。** 同一 `_GroupedRouteEngine` 的 `compute` / `shutdown`，
由 Planner-owned Route Model 管理；源码及检查点同 C2。

**机制与配置。** `_bounded_route_worker_count` 在节点少于 256、终点任务少于
32 或可用 CPU 少于 4 时选单进程。其余情况取
`min(8, max(2, available_cpus // 4), job_count)`；优先按 CPU affinity 计数。
使用 fork `ProcessPoolExecutor`，按请求数平衡分组，进程数不变则复用；
变化时关闭旧 pool。每次计算收集全部分组结果，结束时确定性 shutdown。

**理由与限制。** 这是把独立路由分组分给有界进程并摊薄进程创建成本的机制。
序列化图、worker 内存和等待仍有成本；没有承诺所有规模都获益。它不是
Runner retirement 的 `ThreadPoolExecutor`，也不共享其生命周期或配置。

<a id="c4"></a>
### C4：整数索引的稀疏 capacitated GS assignment

**Owner / scope。** GS planning 的私有分配器
`_ground_station_allocation.py`（历史源路径：`../../src/skyseed_v2/simulator/_ground_station_allocation.py`），
重点为 `_index_primary_problem`、`_select_primary_indexes`、
`_run_primary_successive_shortest_paths`。当前表示已存在于 `1b3d14c`。

**机制。** 对 GS/Satellite 编规范整数索引，仅存可行候选；候选行、每 Satellite
占用列表、选择/距离/势数组共同表示二部 residual graph。已选边的反向边允许
增广路径重分配名额，successive shortest paths 利用势与优先队列找增广路径。
没有容量冲突的全第一选择可直接得到同一最优结果。

**语义与理由。** 目标仍依次为最大分配数、最小累计候选 rank、最小累计全局
Satellite order。编码使用 `rank_scale = GS数 * max(1, Satellite数) + 1`。
同代价解继续使用全局节点顺序、严格距离改善和全部可达节点的 potential
更新，不能用另一个“同样最优”的解替代已接受身份。选择稀疏残量图表示减少
通用对象开销；它不是新 allocation policy，也没有单独测得的收益承诺。

<a id="c5"></a>
### C5：显式 accumulated-deadband 参数策略

状态：以下为 Runner 里程碑时的历史策略；本轮已由 C6 的相对阈值与刷新策略取代。

**Owner / scope。** 公共 `AccumulatedDeadbandParameterPlanningModel` 与
`ParameterKindConfiguration` 位于
`planning/models.py`（历史源路径：`../../src/skyseed_v2/simulator/planning/models.py`），
Planner 拥有有效状态和 memory；场景注入见
[`Starlink simu.py`](../../src/skyseed_v2/simulator/defaults.py)。

**当前配置。** `STARLINK_GEN1_DELAY_DEADBAND_US=191`，
`STARLINK_GEN1_PARAMETER_PERSISTENCE=10 s`，
`STARLINK_GEN1_PARAMETER_MAX_STALENESS=30 s`。这些是 canonical 场景选择；
`ParameterKindConfiguration` 本身默认 deadband/persistence 为零、staleness
为 `None`。`fa280e0` 已包含 191 的场景设置，当前 Model 归属见 `1b3d14c`。
旧场景还使用 `relative_deadband=0.024067890929326655` 与字段 bundling；这些
不是当前 `ParameterKindConfiguration` 的字段，不能从旧配置恢复为当前策略。

**实际更新条件。** 新连接立即使用物理延迟。已有连接用当前物理延迟与上次
effective delay 的差计算 drift；`drift <= deadband` 且未 stale 时保留值并
清空 pending。否则，仅当候选物理延迟值与前次 pending 完全相同才累计
persistence；值变化会重置计时。达到 persistence 或距上次更新达到
max_staleness 时采用当前物理延迟。最终有效参数未变化时不产生参数变化事件。

**理由、证据与限制。** 抑制小波动和短暂候选、限制长期不刷新，是从这些
条件可直接解释的策略作用；没有恢复“为何恰为 191”的独立设计依据。
误差可在等待期超过 deadband；staleness 按观测点判断，不是连续时间硬实时
保证。已有 [参数测试](../../tests/unit/test_planning_reference_models.py)
分别覆盖 accumulated drift、stable pending target 和 staleness；本次未重跑。

## Compiler 与证据表示

<a id="p1"></a>
### P1：在已有执行保证上折叠 semantic precedence

**Owner / scope。** BPF Compiler 的
`_compile_constraints` (historical source: `../../src/skyseed_v2/backend/bpf/compiler.py`)，在
`1b3d14c` 已有折叠机制，`6e8652a` 沿用；输入仍只来自 Plan 和 backend intents。

**机制。** 跨时间关系由 activation frontier 保证，严格跨 phase 关系由
Runner phase 保证，同一 forwarding phase 中适用的关系由 atomic forwarding
generation 保证。无后端效果或相同效果单独计数；剩余关系仍物化精确
`plan-semantic-precedence-fallback` 约束。反向时间或不能由 phase 表示的要求
会失败，不会为性能忽略。

**理由与限制。** 用已有保证及 fold counts 表达关系，避免物化它们的意图
笛卡尔积。这是约束表示选择，不修改 Planner precedence 或 Runner publication
语义。历史说明见 `1b3d14c:skyseed_v2/docs/CURRENT.md`；没有每种 fold 的
隔离性能结果。

<a id="p2"></a>
### P2：紧凑 Planning/Compiler evidence 与流式 gzip JSONL

**Owner / source。** Planning schema 位于
`planning/evidence.py`（历史源路径：`../../src/skyseed_v2/simulator/planning/evidence.py`） 的
`plan_to_data` / `plan_transition_to_data`；机械流读写位于
[`_durable_evidence._open_jsonl`](../../src/skyseed_v2/shared/_durable_evidence.py)。
Producer 的 `_persist_compiler_lineage` / `_compact_compiler_evidence`（历史源路径：`../../src/skyseed_v2/experiment/_realization_producer.py`）
生成 compact Compiler 记录；[Compiler Tool reader](../../tools/observation/bpf_compiler/reader.py)
读取其既有 schema。

**机制与路径。** precedence 端点用 transition-derived event list 的索引对
表示；一个 Route transition 的 previous/current outcome 共享一份 demand
编码。`domain-history/planning.jsonl.gz` 使用流式 JSONL、gzip level 1、固定
`mtime=0` 和空压缩文件名。`compiler.jsonl` 保留 lineage、时间、计数；当前
紧凑投影还提供边界工作量、fold counts、intended cardinalities 和 Zone prefixes。
终端 manifest 封存流的字节/hash、覆盖范围和计数。

**历史与限制。** `1b3d14c` 已采用紧凑 planning/Compiler evidence；该时点
runtime payload 仍在旧 checksum realization handoff 中。`6e8652a` 的当前
artifact-v3 及 compact inspection 投影须按各自 schema 读取，不能把旧包装
测量说成 artifact-v3 的隔离结果。紧凑 history 不含完整 runtime intentions，
也不靠缺失字段推断语义。低压缩级别选择减少压缩计算，代价是压缩比；没有
独立 gzip、RouteDemand 或索引端点的 A/B 数字。

**直接历史测量。** `1b3d14c:skyseed_v2/docs/CURRENT.md` 保留当时 accepted
offline run：外部 wall 847.79 s；runtime packaging 380.484 → 17.932 s；
Planner Tool 315.473 s / 约 4.11 GB → 218.610 s / 约 546 MB；Compiler Tool
351.112 s / 约 8.46 GB → 0.640 s / 约 462 MB。原文未在这些 GB/MB 数值旁
定义统一资源口径，本文不补标 RSS 或磁盘体积，也不重新解释原结果。

## Runner：联合里程碑中的采用机制

R1–R6 的当前源码检查点统一为 `6e8652a`。直接历史与联合测量共用
[Runner 原里程碑](runner-optimization-20260907.md)、
[原 evidence index](runner-optimization-20260907-evidence.json) 和
[原 archive](runner-optimization-20260907-evidence.tar.gz)。本文只链接它们；
中间迭代未各自形成 Git commit，不能仅从 source fingerprint 重建源码。

<a id="r1"></a>
### R1：有限 artifact、一次 Preparation、prepared Main delta

**Owner / source。** BPF 后端
`_RunnerArtifactWriter` (historical source: `../../src/skyseed_v2/backend/bpf/_runner_artifact.py`)、
`BpfRunner` (historical source: `../../src/skyseed_v2/backend/bpf/runner.py`) 和私有
`runtime_realization.py` (historical source: `../../src/skyseed_v2/backend/bpf/_runner/runtime_realization.py`)。
生成 orchestration 使用 writer，Runner 拥有执行 lifecycle；没有新的领域 owner。

**机制与合同。** Producer 完整 drain，writer 归一化已编译事实、累计容量并
封存 `runtime/runner-artifact/{manifest.json,runner-runtime.bin}`。
`BpfRunner(*, compose_project_name, runtime_artifact)` 收 manifest `Path`。
有限 `SimulationTimeline.end_time` 为必填。Preparation 解码全部边界、构造
immutable delta 和 JSON wire command、预写语义表并物理激活 B0；正式运行
从 B1 开始。所有边界执行当前准备好的 delta，不存在 Maintenance、READY
生产/等待、deferred generation completion 或 runtime generation reclaim。

**检查分工。** writer 的 `append` / `_validate_sequence_pair` 在生成阶段检查
predecessor、world/state fingerprint 衔接、等间隔、exact next start、boundary
references 和 packed round trip；finalize 封存结果。
`experiment.execution._read_and_verify_execution` 核对 source/input/environment
身份、manifest/binary hash、schema/seal 和 timeline metadata。
Preparation 打开 mmap 时，`_RunnerArtifact._validate_binary` 检查 header/index
bounds；逐记录解码校验 record hash 和 operation count。Reader 的 indexed
能力不意味着 Main 惰性解码，也不意味着 reader 重做 writer 的序列关系验证。

**理由与代价。** 将有限推导和命令编码移到正式计时前，使 Main 的工作对应
当前变化；完整计划和语义表需要内存，容量不足在生成/Preparation 暴露。
此处不是新增 runtime validator。固定 monotonic origin 加 nominal offset
确保不跳边界、不递归推迟时间原点；它是执行不变量，不单列修复 lag 累积的
加速成果。

<a id="r2"></a>
### R2：logical port、live binding 与 generation 预加载

**Owner / source。** 同 R1；BPF map/program 执行位于
`privileged_runtime_command.py` (historical source: `../../src/skyseed_v2/backend/bpf/_runner/privileged_runtime_command.py`)。
字段及 map 合同见 private Runner README (historical source: `../../src/skyseed_v2/backend/bpf/_runner/README.md`)。

**机制。** artifact-v3 为 node-side endpoint 分配非零 uint32 logical port，
跨 withdraw/recreate 保持稳定。generation semantic maps 存 logical ingress /
egress；小型 binding maps 将 live ifindex 与 logical port 互译。Preparation
按完整 finite demand 预写 generation entries；future veth 在其边界到来前不存在。
`active_generation` 发布决定转发可见性，formal ACTIVE 在整个 Main、withdrawal
与物理删除成功后推进；generation reuse 不伪造 publication。

**代价与限制。** 稳定 identity 使语义预计算不依赖未来 ifindex，换来 binding
间接寻址与全序列语义驻留。capacity demand 同时覆盖累计 semantic entries 和
add-before-withdraw physical high-water。logical port 是后端表示，不是领域
Port；prepared/derived records 和 capacity accumulator 也不是新组件。

<a id="r3"></a>
### R3：持久用户态 helper、retained sockets 与 BPF descriptors

**Owner / source。** `runner._PrivilegedHelper` (historical source: `../../src/skyseed_v2/backend/bpf/runner.py`)
及 privileged command loop (historical source: `../../src/skyseed_v2/backend/bpf/_runner/privileged_runtime_command.py`)，
Linux 执行在 `kernel_netlink.py` (historical source: `../../src/skyseed_v2/backend/bpf/_runner/kernel_netlink.py`)，
startup identity 在 `process_incarnation.py` (historical source: `../../src/skyseed_v2/backend/bpf/_runner/process_incarnation.py`)。

**机制。** Preparation 捕获 PID/start-time/netns，保留 namespace descriptors、
rtnetlink sockets 和 BPF descriptors。长期用户态 helper 从既有通道接收命令，
通过 netlink/BPF syscalls 执行 pre-publication、publication、post-publication
工作；Main 不为每个动作启动 Docker、shell、namespace、ip/tc 子进程。内核
执行网络与 BPF 操作，helper 仍在用户态调度，不是永久驻内核的指令解释器。

**容量与代价。** `_reserve_foundation_descriptors` 按
`7 * node_count + 5 * worker_count + 64` 估算需要的 FD，只在既有 hard limit
内提高 helper 的 `RLIMIT_NOFILE` soft limit。它不修改宿主机 D-Bus/inotify
设置，也不另立环境优化成果。保留身份失效会失败，无 container-restart
恢复；cleanup 必须保留未确认资源的责任并关闭通道及 descriptors。

<a id="r4"></a>
### R4：UP veth 分组退休与当轮 join

**Owner / source。** 现有 helper 的 `_start_retirement_workers`、`_retire_batches`、
`_OWNED_RETIREMENTS`，以及 `kernel_netlink._RouteSocket.delete_group`。
源码链接同 R3；生命周期与 timing-v2 解释见
[producer/runtime contract](../development/experiment-control.md)。

**机制与配置。** Preparation 按有限 connection lifetime 安排 retirement
cohort，创建时只在每对 veth 的一个 endpoint 设置非零 group。Main 已撤销旧
routes/bindings 后，对每个 namespace cohort 发一个 `RTM_DELLINK` group
request；内核 grouped netdevice unregister 包括隐含 peer，直接删除 UP pairs。
`SKYSEED_BPF_RUNTIME_WORKERS` 为 1–64，默认 `min(8, max(1, cpu_count))`，
实际工作还受相关节点数限制。Preparation 初始化有限 helper threads；同一
socket 不由多个当轮任务同时使用，全部任务在 Main 完成前 join。

**理由与限制。** 同 cohort 的注销工作合并，独立 namespace 的等待可重叠。
owned ledger 只记录本 helper 的物理清理责任，失败时保留未确认 ownership；
它不是跨边界 deletion service。候选 rollback 按新建 pair 单独删，因为其
未来 group 可能包含先前已 live 的 pair。并发 task-duration sum 不是 critical
wall time；测量中的八线程不是对所有环境的性能保证。

<a id="r5"></a>
### R5：创建时合并 MAC、queue 与 group 属性

**Owner / source。** `kernel_netlink._RouteSocket.create_veth`（R3 源码链接）。
每个 endpoint 的 `IFLA_NUM_RX_QUEUES` / `IFLA_NUM_TX_QUEUES` 都为 1；MAC
及所属 endpoint 的 `IFLA_GROUP` 随创建请求提交，减少独立配置往返。

**直接证据与代价。** 原 Runner 里程碑记录目标 kernel `7.0.0-30-generic`
检查：默认 allocated RX/TX 为 32/32、effective 为 1/1；显式配置后两者为
1/1。同次 kernel 检查确认 group request 删除两对 UP veth 及其 peers，同时
保留无关 pair/group。原 JSON 在既有 archive 中。单队列是当前执行选择，
不是对其他并行负载的吞吐保证；创建属性与删除变化共同测量，不能给本条
独立 speedup，更不能将全部 creation gain 都归单队列。

<a id="r6"></a>
### R6：非阻塞事件持久化与异步 telemetry identity

**Owner / source。** 既有 observation facilities：
`_NonBlockingEventHistory` (historical source: `../../src/skyseed_v2/backend/bpf/_runner/event_history.py`)
与 `_TrafficTelemetryRecorder` (historical source: `../../src/skyseed_v2/backend/bpf/_runner/traffic_telemetry.py`)。

**机制。** event capture 默认容量 4096，使用有界队列的 `put_nowait`；writer
承担 JSON 与 I/O，正常 flush 间隔为 0.1 s。
identity queue 接收 prepared template reference、installed/withdrawn binding
delta 和 boundary，后台展开 generation/interface identity。B0、publication、
completed/in-flight boundary 分别记录，复用 generation 不伪造切换记录。

**理由与限制。** 将批量序列化与写入移出 Main，但 enqueue、内存和 writer
仍有成本；identity 使用 `SimpleQueue`，不能套用 event queue 的有界保证。
cleanup 执行 drain，事件溢出、writer failure 或 drain 不完整均保留可观察
失败信息。Tool 不可把缺失观测伪装为完整证据；这些设施不接管网络 lifecycle，
也不是换名后的 Maintenance。

## Tools 与部署

<a id="d1"></a>
### D1：从冻结输入运行 detached Tools

**Owner / source。** repository-local
[`tools/observation/_detached_inspection.py`](../../tools/observation/_detached_inspection.py) 的
`_DetachedInspectionExecutor.submit` / `join`，在 `b00fc77` 已存在，当前
checkpoint 保留。默认 `max_workers=2`，任务族是 Planner、Compiler、Observation。

**机制。** fork worker 从已冻结输入持久化私有 detached snapshot，再执行
projection/render/write。该 Tool-only snapshot 编码为 `gzip-pickle-v5`，不是
Runner artifact，更不是允许 Runner 重建 rich domain graph 的输入接口。
输入可来自当前紧凑 history；Tool 不重新推进 Model 或 Compiler。

**理由与限制。** 调用者不必在领域计算进程中渲染，但 `submit` 满额时会阻塞
reap，fork、快照、磁盘与最终 join 均有成本。它不保证所有 Tool 路径都通过
此 executor，也不保证零开销或每次提交非阻塞。具体脚本仍须等待自己的
detached 完成证据；成功 runtime 不被单独 Tool failure 改判。

<a id="d2"></a>
### D2：字节相同 build-context 分组与代表镜像复用（历史 tag 路径）

**Owner / source。** deployment utility
`compose/launcher.py`（历史源路径：`../../src/skyseed_v2/compose/launcher.py`） 的
`_write_compose_build_plan` / `_write_compose_project_scripts`，`fa280e0` 引入，
在 `6e8652a` 保留。当前调用见
`Starlink main.py`（历史源路径：`../../src/skyseed_v2/experiment/workflow.py`）。

**历史机制与路径。** 下述 per-service alias 路径在新轮由 [D4](#d4) 替代；
其原有测量和出处保持不变。对每个 context 按排序后的相对路径与文件字节计算 SHA-256，
将长度编码在摘要输入中。`runtime/skyseed-build-plan.txt` 记录 shared base
prerequisite、代表 service 和 alias。生成的 startup 先完成必要构建，再给
字节相同的组复用 image tag，并检查所需 image identity 与 project label；
之后一次 `up -d --no-build`。

**理由与限制。** 避免同样的生成 context 重复构建，不合并不同 service 或
改变部署身份；不是按近似内容分组。若 live project 下 image 缺失或过期，
启动失败而不暗中重建。Mini 当前不调用 build-plan writer，走普通
`up -d --build`；不能把 Starlink 的采用范围扩成所有可部署 Example。
没有仅归本机制的当前 S3 build speedup。

<a id="d3"></a>
### D3：一个 Compose project；并发数须区分入口

**Owner / source。** 可选 `ComposeProjectLauncher` 与 canonical 生成控制
共同位于 D2 源文件，但调用路径不同。前者不拥有 Runner lifecycle，后者不
调用该公共 launcher。历史对照可见
[Compose v2 S4 实验](s4-scale-compose-v2-experiment.md)，只按其原环境解释。

| 当前入口（`6e8652a`） | 实际行为 |
|---|---|
| 可选 `ComposeProjectLauncher` | 默认 `strategy=SINGLE`、`parallel_limit=16`，命令显式带 `--parallel 16`；`BATCHED` 仅供调用者显式选择，默认 batch size 16。 |
| Starlink Compiler 生成的 `start.sh` | 单一 `docker compose -p ... -f ...` project，代表构建后一次 `up -d --no-build`；未设置 `--parallel` 或 `COMPOSE_PARALLEL_LIMIT`。 |
| Mini / 静态教学 Example 的普通 `start.sh` | 同一薄脚本 writer 的单项目 `up -d --build` 路径；同样没有固定并发值。 |

**理由与证据边界。** 单项目保留完整依赖与服务身份；程序式 launcher 用
有界并发限制突发请求，但不能据此声称 canonical scripts 也固定为 16。
当前脚本未钉定的并发行为依赖调用环境及 Compose；既有 S3 记录不足以给它
补一个统一数值。本记录不修改脚本或配置。旧记录中的 Compose-v1 或
`DOCKER_BUILDKIT=0` 兼容处理仅属当时实验，不能恢复为当前要求。

完整 Runner 操作链仅指 Mini 与 Starlink Compiler：`main.py` 完成 output、
realizations、artifact 和 controls；`start.sh` 部署，`run.sh` 启动 Runner/Traffic，
`launch.sh` 合并 start/run，`cleanup.sh` 独立。`complete_static_emulator`
可部署，但保持 start/cleanup 的静态教学流程。

## 宿主机配置记录

H1–H3 的历史背景直接来自 `fa280e0` 的旧 system-overview 第 76–143 行，
旧值和原因如下。H1–H4 的值/路径为当时用户提供的 Work 核验结论，不伪称本次
重新测得；没有恢复 H4 的引入 commit 或精确批准日期。后续直接检查按原日期
单独记录。操作步骤统一放在 [Linux host guide](../user-guides/linux-host.md)，
本节保留理由、状态与证据。这些选择由宿主机管理员维护，不属于
Simulator/Planner/Compiler/Runner API。

<a id="h1"></a>
### H1：D-Bus 每用户连接配置 256 → 512

历史记录：约千容器规模启动时，UID 0 的 D-Bus connections 达到原每用户
上限。采用更大的配置为容器生命周期控制面提供余量，也允许更多连接资源
占用。Work 核对 `/etc/dbus-1/system-local.conf` 中
`max_connections_per_user=512`；这是配置文件观察，不是新的 bus 内部有效
limit 查询。没有单独吞吐或延迟对照，也未恢复 bus reload 的独立执行证据。

<a id="h2"></a>
### H2：inotify instances 128 → 1024

历史记录：容器生命周期活动发生 inotify instance exhaustion 与 containerd
资源警告。采用更高每用户实例上限为 runtime monitoring 提供余量，代价是
允许更多内核资源使用。Work 核对
`/etc/sysctl.d/99-skyseed-s3-container-control.conf` 中
`fs.inotify.max_user_instances=1024`，并报告当前 sysctl 一致。
这不是 `max_user_watches` 或 Runner FD limit 的同义设置，不能混记。

<a id="h3"></a>
### H3：Docker cgroup driver 从 systemd 改为 cgroupfs

历史记录：逐容器创建 transient `docker-*.scope` 的 systemd 路径出现大量
超时。本机选择让 Docker/runc 直接管理 Docker container cgroups，仍用
cgroup v2，保留 CPU/memory/PID cgroup 隔离；逐容器 systemd scope 的可见性
及统一管理相应减少。

Work 核对 `/etc/docker/daemon.json` 的 `exec-opts` 选择
`native.cgroupdriver=cgroupfs`，并报告 `docker info` 显示 cgroupfs / v2。
[更早的 Ubuntu 环境记录](native-linux-ubuntu-24-validation.md) 中的 systemd / v2
属于历史，不改写它。没有把此次选择与 Runner syscall 改造相混的收益归因。

<a id="h4"></a>
### H4：本机 NVMe 与 bind-mount 存储布局

**Work 核验事实。** `/etc/fstab` 将 NVMe 存储挂到 `/srv/skyseed-hot`，其下
Docker/containerd 目录分别 bind-mount 到 `/var/lib/docker` 和
`/var/lib/containerd`。对应服务的 `skyseed-hot-storage.conf` drop-ins 声明
mount dependencies。`/etc/systemd/system/home-lionheart.mount` 将
`/srv/skyseed-hot/home-lionheart` bind 到 `/home/lionheart`；Work 已核对当前
挂载。设备标识和这些绝对路径属于本机，不是项目配置或通用安装参数。

**机制、理由与限制。** bind mounts 保持消费者路径，服务依赖保证先有目标
存储再启动服务；将热数据放到 NVMe 可理解为 I/O 放置选择，这是机制推断，
不是恢复出的独立批准理由或测量。没有恢复迁移前后对照、引入 commit、
精确设备标识或完整 mount 原始输出。它不解决 D-Bus/inotify 限额，不能将
其性能效果计入那些控制面调整，更不能混同 R3 的 helper descriptor reservation。

### Read-only workstation observation: 18 September 2026

This is a new inspection, not a claim that every setting was captured during the
previous six-example campaign. No settings or mounts were changed for this check.

| Item | Observed value |
|---|---|
| Host | Ubuntu 24.04.4; Linux 7.0.0-31-generic; x86_64; 32 logical CPUs; 60.5 GiB RAM |
| Docker / Compose plugin | Engine 29.6.2; Compose v5.3.1 (`docker compose`) |
| Cgroups | cgroupfs driver, cgroup v2 |
| inotify | 1,024 instances/user; 65,536 watches/user, effective sysctl values |
| System-bus file | `max_connections_per_user=512`; configured value, no live internal-limit query |
| Docker service descriptor hard limit | `LimitNOFILE=524288`; actual helper limits must be inspected separately |
| Host collector library | Ubuntu `libbpf1` 1:1.3.0-2build2 |
| Storage | NVMe ext4 bind mounts for Docker, containerd and the user's home; bpffs mounted |

The [scale reference](../user-guides/scale-and-resources.md) reports the prior campaign's timings
and shared-host memory observations under their original conditions.

<a id="h5"></a>
### H5：原生 IPv4 ARP 容量与集中配置（2026-09-22）

**理由与批准。** 原生 S3/S4 具有数千个跨 namespace 的邻居项。21 September 的
S3 GS↔Zone 尝试在原 128/512/1024 阈值下出现实际 ARP `table_fulls` 增长、邻居解析
失败及 `RTM_NEWNEIGH: No buffer space available`。此后的严重宿主机压力原因仍未
查清，不能把它全部归为协议收敛，也不能断言提高阈值导致事故。后续 S4 使用
8192/16384/32768，未观察到新的 ARP table-full 增量；这些是当时场景的容量值，
不是通用最低要求。[S3](native-s3-experiments.md) 和 [S4](native-s4-experiments.md)
保留原日期、运行条件、临时提高与恢复记录，不以本次持久化批准改写它们。

用户随后批准本工作站长期保留 8192/16384/32768，并在 2026-09-22 明确将其放入
已有 `/etc/sysctl.d/99-skyseed-s3-container-control.conf`，与 inotify instances 共用
同一文件，保留无关条目及任何更大值，不新增重复配置文件。先前交接中的独立
neighbor-capacity 文件仅为未安装的草案；本次批准的单文件位置取代该草案建议。

**2026-09-22 首次检查（09:02 UTC）与状态。** 当次只读检查 `/etc/sysctl.conf` 和存在的
`/etc/sysctl.d`、`/usr/lib/sysctl.d`、`/lib/sysctl.d`；`/run/sysctl.d` 与
`/usr/local/lib/sysctl.d` 不存在。未发现 ARP 阈值的持久赋值、冲突或更大配置值。
`/lib/sysctl.d` 与 `/usr/lib/sysctl.d` 是同一目录；`99-sysctl.conf` 指向
`/etc/sysctl.conf`，重复读取这些路径不是重复设置。

| 设置 | 文件观察 | live 值 | 本次状态 |
|---|---|---:|---|
| `fs.inotify.max_user_instances` | 现有 `99-skyseed-s3-container-control.conf` 为 1024 | 1024 | 保留，已有持久配置 |
| `fs.inotify.max_user_watches` | vendor `30-tracker.conf` 为 65536 | 65536 | 保留，不复制到工作站文件 |
| `gc_thresh1 / gc_thresh2 / gc_thresh3` | 未配置；目标文件目前只有 inotify 一行 | 8192 / 16384 / 32768 | **持久化 pending** |

目标文件为 root:root、0644；`sudo -n true` 返回 `sudo: a password is required`。
因此本次没有修改 `/etc` 或 live 值，不能报告已完成持久化。管理员剩余工作为按
[现有文件的 edit/apply/verification 步骤](../user-guides/linux-host.md#approved-sysctl-settings)
加入三项批准值（如有更大值则保留），再核对文件、冲突与 live 值。无需重启 Docker、
D-Bus 或宿主机；本次没有执行任何重启。

检查快照及待应用的完整四行草案位于仓库外：
`/home/lionheart/workspace/skyseed-local/implementation-baselines/host-settings-20260922T090259Z/`。
`inspection.json` 保留匹配赋值、来源文件摘要、live 值和权限结果；草案尚未安装。

**2026-09-22 后续只读复核（09:12 UTC）。** 最终提交前复核发现，已有
`99-skyseed-s3-container-control.conf` 现已包含 inotify instances 1024，以及 ARP
阈值 8192/16384/32768；目标文件仍是唯一 ARP 配置来源，没有新增重复文件或冲突赋值。
对应 live 值一致，inotify watches 仍为 vendor 文件配置的 65536。持久配置已写入，
不再等待管理员补写这三项；此前 pending 状态仅描述上面的首次检查。本次助手没有
写入宿主机配置，也没有重启 Docker、D-Bus 或宿主机；未做重启后的生效验证。
复核证据：
`/home/lionheart/workspace/skyseed-local/implementation-baselines/programmable-runtime-closeout-20260922T073519Z/final-commit-20260922T091205Z/host-verification.json`。

### 宿主机回退含义（未执行）

回退应是单独安排的维护操作：保存相关配置与数据、确认容器和依赖服务的
停机边界，再只调整目标设置。D-Bus/inotify 的历史值是 256/128，但较小
限额会重新限制大规模并发；需分别验证配置与实际生效状态。

Docker driver 回退应保留 `daemon.json` 的所有无关键及其他 `exec-opts`，仅
将目标 cgroup-driver 设置恢复为原选择，并在受控服务重启后核验。旧文档
直接删除整个 `daemon.json` 的命令不能作为通用回退步骤复制。存储回退需
单独核对数据、fstab/bind mounts、home mount unit 和 service dependencies，
避免切换路径时暴露空目录或旧数据；本文不提供未经核验的设备命令。

## 已替代机制、联合结果与剩余缺口

**已采用但现已替代。** `1b3d14c` 的 rich-realization checksum handoff 和
执行入口全量解码 tuple 由 artifact-v3 / Preparation 解码替代；旧 Runner
Maintenance/READY/deferred completion/reclaim 也已移除。详细计时基线的
逐对串行退休由当前按 namespace 的 grouped deletion 替代。历史结果保留
原含义，不把这些旧机制作为当前路径；未采用试验不列入当前决策。

**联合 Runner 结果。** 原里程碑的 detailed-timing baseline → work reduction
记录 Main p50/p95/max 从 3.555/4.018/4.206 s 到 1.059/1.252/1.288 s；相同
23,120 对退休，deletion requests 从 23,120 到 15,033。它们是整组变化的
S3 结果，不是 R1–R6 各自的独立收益。准确值、输入一致性、Traffic WARN、
archive checksum 和逐文件 hash 均沿用原 evidence index，不产生新 archive。

**仍保留的证据缺口。** 191 的精确选值理由、早期各机制的隔离收益、中间
Runner 可重建源码、canonical scripts 的固定启动并发值，以及存储迁移的
独立历史/性能证据均不能补造。宿主机当前状态依赖本次提供的 Work 核验，
不是本次文档任务的 live 验证结果。普通 bug fix、显然重复的扫描/比较删除、
小型防御检查和 incidental cleanup 不在本文单列为优化成果。

<a id="c6"></a>
### C6：本轮批准的接触、准备与参数语义及其物理计算

状态：在 `911e4a45ee8700736975bfee2fad61613de8f089` 基线之后实现的首轮记录；
仍随累计未提交 diff 保留，不能从后续局部 review 推导整个里程碑已验收。
不改变前述 Runner 历史结果。原 C4/C5 描述保留为历史，当前合同
以 accepted-design.md 为准。

物理 Model 使用圆轨道全相位距离/LOS 上界证明稳定 ISL，其他接触以采样
区间中点和保守运动位移界证明整个区间。未知区间不满足准入；有限 proof end
不是消失。保存接触区间，复用轨道、GS/Zone 静态几何，使用有界数组块和安全
筛选，避免为所有未来 pair 创建候选对象。非圆轨道的连续运动证明未建立。

Planner 选择前检查准备后的有效服务，并在准备期间预留两端容量。GS 首先
最大化站点覆盖，再保留有效 incumbent/pending，最后比较质量。满端口必须
付出 break-before-make 空窗。参考 5 秒准备、180/60 秒有效服务分别是简化
ISL 设置与工程 GS 假设，不是商业设备测量。10% 相对延迟阈值和 120 秒刷新
取代固定微秒/整数目标 persistence；改变事件数量属于策略变化，不能当作
等价工作量的算法加速。最小测试分别核对未来准入、真实 ISL 变化、容量与
准备、阈值边界；紧凑物理计算与小图 GS 分配有独立穷举参考。

源码：`simulator/models/_contact_prediction.py`、`_batched_geometry.py`、
`simulator/planning/models.py`、`planner.py`、`simulator/_ground_station_allocation.py`。
这里的源码路径相对 `src/skyseed_v2/`。独立机制加速比例未证明。

<a id="c7"></a>
### C7：有限帧批次、增量索引和材料/Tool 分离

状态与基线同 C6。32 帧是工程起点，不是控制周期。取消独立 50 秒等待；
预历史/尾部仅由准备和最短服务依赖推导，物理预测不增加正式 Plan 时间。
Planner 按时间推进；Compiler 仅在 lowering 中重放一次并复用上一 realization
的私有索引。Route Model/engine 复用未变的图与整数索引，保留按终点 BFS 和
确定性最少跳数。已有进程池改用 spawn，与有界异步证据写入共存。

证据写入保持顺序并复用压缩流；Starlink 下一批语义计算可与上一批 artifact /
Traffic 打包重叠。正常生成保留序列、引用、身份、hash 与真实 seal，完整
encode/decode/对象比较转入 codec 测试和离线 qualification。容量累积按相关
变化复用计数；Mini 也使用增量 Traffic 构建。runtime package 继续使用已有
Compose context 去重和 artifact 目录移动，无第二生成算法。

材料完成状态在 Tools 前可见，CLI 为 qualification 仍收齐静态与有限后台 Tool
结果。Planner Tool 避免 reader/projection 双重重放和重复整包编码；Compiler
Tool 复用一次 observation 解析。ready、Tools 和总耗时分开，worker 总时长
与语义阶段重叠，不相加。源码位于现有 experiment Producer、BPF Compiler /
artifact、shared durable evidence 与 Tools；没有新 owner、daemon 或 Runner phase。

该离线轮不能证明单项机制 speedup、商业硬件准备时延或新的 live Runner/S3 性能。
历史 859.590 秒是含 Tools/orchestration 的外层总时长，不是 Producer-only。

本轮最终离线测量摘要保留于
[`qualification-producer-final-20260907T134951Z`](/home/lionheart/workspace/skyseed-local/experiments/phase4b-1-20260917T144814Z/retained/five-shell-20260910/qualification/comparisons/producer/)：
材料完成 360.058 s，随后 Tools 102.584 s，main 总计 462.642 s，含解释器
启动/退出的外层总计 463.781 s。0.5 s 间隔的进程树采样包含 Route/Tool
worker，峰值 PSS 1,359,085 KiB；RSS 1,490,092 KiB 会重复计算共享页。
32 帧配置未另行调参，保留 121 transitions / 4 Plans / 4 realizations。
完整旧 diff、source snapshot 和诊断日志已删除；摘要保留原计时口径。当前状态和
验证边界见 [CURRENT](../CURRENT.md)。
这是策略变化与实现优化的历史联合记录，不归因于任何单一机制；当前审查范围
与状态由 CURRENT 统一记录。

<a id="c8"></a>
### C8：最终优化轮的精确复用、表示与执行粒度

状态：2026-09-07/08 在 C6/C7 当时未提交实现上实施；后续 Work 独立审查在该轮
范围内未发现阻碍收尾的问题。累计里程碑后来已接受；本节保留当时测量边界。
本轮保留同一有限时间线、准入/准备、GS 覆盖/连续性/质量、路由和 Runner
合同；不增加 owner、运行阶段或公共配置。开始时的完整 diff 和源码快照已删除；阶段测量及核验摘要见
[`qualification-final-optimization-20260907T152421Z`](/home/lionheart/workspace/skyseed-local/experiments/phase4b-1-20260917T144814Z/retained/five-shell-20260910/qualification/comparisons/producer-final/)。

**GS 精确复用。** 既有 allocator 先用交替路径证明 retained assignment 是否
已达到最大站点覆盖。若仍可增加覆盖，且 retained 在容量内、每站最多一条，
这些独立 assignment 构成原 successive shortest path 的严格低成本前缀。
批量应用此前缀，在最后一次增广前恢复全局最短势，再由原全局 residual
算法处理剩余变化。保留全局节点顺序与质量权重，不能用“可用 incumbent
总是保留”取代覆盖目标；一般冗余/容量冲突输入仍走完整求解。小图穷举、
精确起始源码对照及保留 S3 帧核对覆盖恢复、损失和确定性 ties。

**物理计算与 contention。** 既有 Model 从已校验的静态身份、位置、配置和
数组几何冻结当前候选；公共构造器仍校验调用方输入，候选和接触证据没有
删减。表面数组采用有界 720 行 tile，减少线程在 Python/NumPy 间的交接。
同一帧的受控实验记录在 `diagnostics/contention.py`：旧实现物理观测独立
中位 0.246 s，与 artifact 线程重叠为 3.185 s，而其自身 thread CPU 为
0.289 s；无 I/O 的 Python competitor 也复现慢化。该证据支持解释器调度
竞争，不能把全部差额归因于 GC 或内存带宽。表示优化后的 tile 对照保持
候选完全相同：64/720 行在 Python competitor 下分别为 3.522/1.101 s，
独立约 0.165/0.173 s。720 是该受控输入下采用的粒度，不是通用最优值；
保留有界 artifact/evidence 线程重叠，不修改 GC、worker 上限或宿主机设置。
合并优化后的同输入复测见 [diagnostics/after/results.json](/home/lionheart/workspace/skyseed-local/experiments/phase4b-1-20260917T144814Z/retained/five-shell-20260910/qualification/comparisons/producer-final/diagnostics/after/results.json)：物理独立/与
artifact 重叠中位 0.185/0.989 s，重叠时自身 thread CPU 0.192 s；同一
Plan 的独立 artifact append 为 5.369 s（起始 14.694 s）。这是组合改动的
定向对照，不把收益逐项归因，也不等同于最终生成耗时。

**派生索引。** Route engine 仅保留当前请求的路径，以 ISL 子图和相关 GS
终点/源端接入为依赖；GS 不转发其他 GS 的 transit traffic，因此无关接入
变化无需使所有终点失效。Compiler 复用每时点端点→已有 Connection 身份
映射；artifact 一次分组批次内操作与约束。已有 runtime 模块内的
`_BoundaryStateIndexes` 在容量累积和有限 Preparation 中应用 delta。
容量按节点/目的地址/出口引用计数，保留共享目的地址、冲突检测、先加后删
高水位和 generation 语义。Preparation 仍完成全部有限计划与预写入，Main
仍只执行 prepared delta，所有当轮退休任务 join 后才完成 ACTIVE。

**Tool 表示。** Planner 从已验证选择直接生成 browser rows，同一 RouteDemand
的展示 JSON/hash 只在本次 Tool invocation 内派生一次；Traffic 保留唯一
必要重放与 Plan 输入/输出指纹核对。Runner projection 负责一次调用方输入
隔离，内部视图复用已隔离内容；inventory/checksums 共享 payload digest。
这些是既有 Tools 的私有数据职责，领域生成不依赖展示完成。

[diagnostics/retained-reference-results.json](/home/lionheart/workspace/skyseed-local/experiments/phase4b-1-20260917T144814Z/retained/five-shell-20260910/qualification/comparisons/producer-final/diagnostics/retained-reference-results.json) 保存起始源码 SHA256 核对后的
精确对照：600 小图、7 个保留 S3 帧、32 个完整归一化边界及 121 个容量边界。
另有 [diagnostics/seed-reference-results.json](/home/lionheart/workspace/skyseed-local/experiments/phase4b-1-20260917T144814Z/retained/five-shell-20260910/qualification/comparisons/producer-final/diagnostics/seed-reference-results.json) 的 4,000 个精确 tie 对照，
其中 3,647 个应用 retained 前缀，344 个直接证明 retained 最优。
同一批已解码边界的容量累积为 3.355 s，起始实现为 18.937 s；这是定向诊断，
不是端到端或单项部署收益。早期未完成的诊断日志已删除；不把它们作为通过依据。
最终完整链路与内存测量以 [CURRENT](../CURRENT.md) 和本轮原始记录为准；
2026-09-08 已完成该轮 fresh S3 全链路，随后获得上述限定范围的 Work review。材料完成为
222.054 s，含 Tools 的外层生成 315.378 s；与 C7 相同的完整 artifact
manifest、binary hash、121 boundaries 和 218,731 operations 已精确核对。
Preparation 90.335 s（含 B0），正式 120 个 Main 的 p50/p95/max 为
0.118/0.200/0.225 s；所有当前边界删除均 join，Runner/部署清理成功。
新 Traffic 为 WARN：18 episodes、2,486 sends、1,649 replies、837 losses，
819 个 timeout/network-error observations 位于预期不可达区间；没有
infrastructure/persistent-blackhole failure，遥测无丢失。它不继承历史验收。
当时保留了完整原始包和 Tool taxonomy。后续经授权清理了可重建的部署和
Tool payload；该历史轮如今仅保留选定 taxonomy/测量摘要，完整原始输入和遥测不再保留。不能把所有损失
归为正常或抹去未分类记录。
本轮没有证明每项机制的独立收益；Traffic Tool 148.418 s、采样进程树 PSS
4.366 GiB 是当时记录的明确成本。后续 D4–D6 来自用户另一次明确授权，
不把这些历史计时重写为新轮结果。


<a id="d4"></a>
### D4：生成 Compose 的共享内容镜像与唯一身份核对

**Owner / source。** 2026-09-08 获准的部署/三条件轮，仍在
`compose/launcher.py`（历史源路径：`../../src/skyseed_v2/compose/launcher.py`） 的私有 packaging
与 generated controls 内。公共 ComposeProjectLauncher 不编译或改写输入项目，
不拥有 Runner。开工的 96 tracked + 3 untracked 修改由
[保留的历史测量](/home/lionheart/workspace/skyseed-local/experiments/phase4b-1-20260917T144814Z/retained/five-shell-20260910/qualification/comparisons/deployment-matrix/matrix-measurements.json)
保留测量身份；同目录旧 binary diff 与未跟踪原文已删除。

**理由。** 前轮 S3 有 1,075 个节点 context、两个相同内容组，仍执行 1,073 次
image tag。其 shell trace 推导的阻塞区间约为 inspect 219.718 s、tag 184.694 s、
三次 build 15.449 s、Compose up 110.493 s；不是 Docker daemon 内部 CPU profile。

**实现。** context 摘要包含排序后的路径、模式、长度与字节；不支持的 symlink
显式拒绝。每个内容组只有代表保留 build，组内各 service 指向同一项目作用域的
内容镜像。原 container_name、labels、environment、mounts、依赖和网络配置
保持各自值。startup 核对每个唯一镜像的 ID、生成镜像的 project label 和完整
Compose image set；外部镜像可以没有 project label，
必要时构建 prerequisite 与各代表，不再复制 tags。live project 下 image 过期、
容器 inventory 失败、构建失败均停止，保留实际失败 status。异质 context 和
失败路径由行为测试覆盖。build-plan v2 是私有生成控制材料，不是新领域输入。

**限制。** 共享静态镜像不表示共享容器或运行身份；不按近似内容合并。Mini 的
普通 build 路径不变。没有统一覆盖所有入口的新增并发默认值。最终规模计时和
容器事件来自同轮独立 qualification，不把消除 CLI 数量拆成虚构的单项实测速率。

<a id="d5"></a>
### D5：Traffic 的语义读取、验证复用与紧凑记录写入

**Owner / source。** 既有 repository Tools：`tools/observation/planner/reader.py` 的私有
`_PlanHistoryRead` 只携带 Plan、transition fingerprints 和 frontiers，为 Traffic
读取 sealed history 的语义部分；它不是新公共数据合同。完整 Planner inspection
继续由原 reader/Tool 重建。Traffic direct collection 验证完整 Plan replay 后，
立即复用该结果执行投影；公开的可变 snapshot 投影仍重新验证。C8 所减少的是
上游 history reader 的额外 replay；本轮又消除了 Traffic reader→projection
之间剩余的一次重复 replay，原历史数据和测量不重写。

**证据。** [detached 完整语义对照](/home/lionheart/workspace/skyseed-local/experiments/phase4b-1-20260917T144814Z/retained/five-shell-20260910/qualification/comparisons/deployment-matrix/diagnostics/traffic-semantic-comparison.json)
记录当时以原 S3 raw 输入完成的全部 JSON 字段对照，只排除
自身计时/资源 accounting，结果 PASS。该诊断 outer 为 82.654 s，原完整运行
的 Traffic Tool 为 148.418 s；两者不是 fresh-network 对照。JSON 从 600,332,172
变为 382,953,832 bytes。独立 `/usr/bin/time` 记录最大 RSS 2,105,108 KiB，
不是 PSS，也不能与旧 process-tree PSS 直接作为同口径比例。

**实现与取舍。** 避免重建 Traffic 不消费的 ISL/GS Model inspection 图；它们的
原记录继续保留，sealed stream hash、envelope/时间对齐、Plan 身份、世界/状态
lineage、语义事件和最终覆盖检查保留。JSON 不删除事件或字段：按 section 和
record 分割，记录内部使用标准 C encoder，流式写入 compact JSON；原 HTML
表格与分类保持不变。其他 Tool 的缩进 JSON 也按流写入，保持原字节格式。
Planner/Traffic hash 使用流式文件摘要，inventory/checksum 共用 payload hash。

中间版本 cProfile 确认完整 Model inspection 重建和缩进编码的显著累计成本；
profile 有额外调度/函数调用开销且不是最终版 performance baseline，不用其
嵌套时长相加或归因单项 speedup。该 profile 没有单独的完整源码快照；最终
语义对照和各条件正式 baseline 则记录实际 source fingerprint。当前保留的
完整 Traffic JSON 与 in-memory detached snapshot 仍随证据量增长；这不是
流式实时 Traffic 服务，也不声称内存与 horizon 无关。

该精确对照之后，只更正了一个解释 caption：从“activation 后一秒”改为
“actual Main transition 期间”，与已有实际 Main start/end 分类范围一致。
[caption/source 记录](/home/lionheart/workspace/skyseed-local/experiments/phase4b-1-20260917T144814Z/retained/five-shell-20260910/qualification/comparisons/deployment-matrix/diagnostics/traffic-caption-correction.json)
保留前后 SHA；没有改变分类、事件或评分。fresh 三条件使用更正后的源码。


<a id="d6"></a>
### D6：S1 与 cadence 的显式实验配置

Example 保留 `emu.py` / `simu.py` / `main.py` 流程，增加 `--shell` 与
`--observation-interval-seconds`；选择写入 output 的输入副本。Timeline 派生
121/601 个时点和 4/19 个 32-frame Plans，不引入第二条运行路径。准备、
服务证明、参数刷新均保留秒单位，Traffic 发送间隔独立于观察 cadence。

初次 S1 generation 在 Planner 入口拒绝缺省 ISL capability，没有进入部署。
修正为在既有 `shells.yaml` 中给全部 1,584 个 S1 Satellites 明确声明
`maximum_simultaneous_inter_satellite_links: 4`，与批准的可比政策/S3 相同。
GS capability 2、所有几何和其余 shell 文本不变。这是模拟参数假设，不是
商用硬件声明；没有用 runtime fallback 掩盖缺失物理输入。
[前后配置身份与失败范围](/home/lionheart/workspace/skyseed-local/experiments/phase4b-1-20260917T144814Z/retained/five-shell-20260910/qualification/comparisons/deployment-matrix/diagnostics/s1-capability-correction.json)
和 seven-case 配置检查保留。已完成 S3 的生产源码与所选 S3 输入未受影响，
其原始完整 shell 文件副本当时作为执行身份依据；本次只保留对应比较摘要。

本轮没有新增公共领域 owner、schema 或库 export；`_PlanHistoryRead` 为既有
Tool reader 的私有记录，build-plan v2 为既有 deployment packaging 的内部材料。
三条件实测、系统设置/回滚与局限见[性能视图](../history/qualification-performance.md)。

Compiler viewer 后续仅把 S3-only / READY caption 改为 scenario / completed
ACTIVE。已完成 S3/5 s 与 S1/5 s 的 JSON 数据字节及计时不变，原始页面和
checksum 已随历史 raw bundle 删除；保留的 caption 范围及前后身份记录在
[原始校正记录](/home/lionheart/workspace/skyseed-local/experiments/phase4b-1-20260917T144814Z/retained/five-shell-20260910/qualification/comparisons/deployment-matrix/diagnostics/compiler-caption-correction.json)。
S1/1 s 直接使用更正后的 renderer，不需要重跑未受影响的网络生命周期。

<a id="b1"></a>
## B1. Consecutive finite delivery and local content sharing (2026-09-08)

Status: approved scope, implemented in the existing uncommitted tree; focused
checks passed; final A/B qualifications and independent Work review are pending
at the time this decision entry is written. Actual results are maintained in
[CURRENT](../CURRENT.md) and the [performance view](../history/qualification-performance.md).
Earlier R/D/H records retain their original scope, measurements and acceptance.

The user approved fixed-node sequential sealed delivery, with no online
recalibration or replacement of loaded futures. The accepted owner structure
remains Simulator→Planner and BPF backend→Compiler/Runner. `Planner.flush()`
publishes a prefix without finalizing; the Producer and artifact writer retain
explicit continuation state. `BpfRunner.append_batch(Path)` registers one next
sealed manifest. Optional artifact delivery metadata binds predecessor identity,
state, time and generation. No new public class, root export, domain entity or
independent lifecycle authority was introduced.

Initial Preparation initializes infrastructure and executes B0 once. Main owns
all formal activation/withdrawal work under one fixed clock. Runner/backend
Maintenance loads/prewrites a successor at T−αD, marks complete READY, and
reclaims actually retired runtime data under reader and version-reference
checks. Missing READY at the nominal deadline fails/incompletes the run without
waiting or resetting time. The helper has bounded prewrite/reclaim units and
Main channel priority. Physical deletion workers and their ledger remain in
that helper, and every current-boundary deletion joins before Main completes.
Continuation uses stable per-connection retirement groups, trading the one-shot
cross-connection cohort aggregation for correct ownership without known future
withdrawals. Terminal Cleanup still ends the session.

Qualification B selects 5-second sampling, 32-frame internal publication,
300-second delivered coverage, α=0.75 and capacity factor 2. These distinct
controls are visible in the standalone S1–S5 Example. First coverage `[0,300)`
and final `[300,600]` provide 60+61 timestamps, one B0 and 120 Main. A bounded
estimate from dated S1/1 s decode/construction/prewrite is about 101 seconds;
the same-cadence S1/5 s reference gives about 141 seconds for a 61-boundary
successor at the new node count. The 225-second lead exceeds both, with about
84 seconds over the larger estimate; it is neither a scaling proof nor an optimum. Fixed
resident admission is based on the first batch and configuration, not an
assumed entire future session. Oversized or incompatible batches fail closed.

Before B deployment, sealed-demand inspection found 447 successor nodes whose
route demand exceeded twice their own first-batch demand (maximum ratio 10.533).
That per-node historical basis prevented legitimate forwarding-work migration.
The private reservation now uses the first-batch peak of each typed entity kind,
with the same factor 2; exact sealed demand is not rewritten. B's successor
maximum route/peer-peak ratio is 1.196. Fixed route and active map reservations
are 34,398,920 and 6,432,720 entries, with 53,606 binding entries. The larger
reservation trades memory for bounded peer migration; it is not a capacity
optimum or a guarantee that arbitrary future batches fit. The original and
corrected static admission records remain beside the failed first B generation.
Actual runtime admission and memory still require the qualified invocation.

Work reduction is measured separately from relocation. Construction reuses
unchanged route/access dictionaries and derived node records, indexes static
capacity once per batch, and shares immutable per-node generation content to
avoid repeated table writes. A new node-version lookup preserves the tc-BPF
forwarding path. Continuation adds active-generation reader accounting and safe
retirement; it does not substitute a different dataplane. A wholesale table
replacement was not adopted because it would require broader publication and
reclamation changes than the local, independently testable sharing mechanism.

Physical World Tool removes the private owned-history deepcopy while the public
projection retains input isolation. ISL reuse proves retained selection remains
maximal under current candidate shape and endpoint policy/capacity constraints;
otherwise it recomputes. GS removes redundant candidate wrappers and defers an
unneeded satellite index. Route reuse explicitly keys assignments, demands and
graph dependencies and retains equal immutable outcomes without fabricated
changes. Physical observations and formal timestamps are never skipped.

Selected offline/reference, continuity and runtime measurements are retained in the
[normal output qualification directory](/home/lionheart/workspace/skyseed-local/experiments/phase4b-1-20260917T144814Z/retained/five-shell-20260910/qualification/README.md).
Full original source/raw/Tool bundles and duplicate aggregate portals were removed
at accepted closure. Host changes retain their recorded original scope in this
history; none is introduced by closure.

NOT PROVEN: indefinite operation, unrestricted node changes, online calibration,
optimal lead fraction, linear scaling, or zero-loss Traffic. Generation/model
spans can overlap; moving a span into Maintenance is not eliminating its work.

<a id="focused-correction"></a>
## 2026-09-10 accepted correction and presentation closure

Authorized scope is the final focused correction. Existing Planner/Models retain
GS and Route truth; physical-core caches remain derived per-world/per-frame data.
The exact GS kernel now seeds a feasible circulation and cancels negative residual
cycles (queue Bellman-Ford with early predecessor-cycle detection); absence of a
negative cycle is the optimality certificate. The unchanged cold SSP remains the
comparison reference. Equal sums at the fifth objective can choose different
assignments under canonical residual traversal; this does not add a sixth policy
objective. The differential check evaluates all five integers on identical actual
candidates and incumbents, and records every changed pair. Separate cold Route
and Compiler replays check their consumers across the complete formal horizon.
Shared GS capacity is global and is never duplicated per Shell.

Raw Traffic v2 records update only changed endpoints and seal generation membership.
Reader validates one generation at a time and carries shared endpoint/incarnation
ranges through projection v6. Existing Recorder threads and offline Tools keep their
ownership and nonblocking separation. Generated teardown now uses explicit batches
of at most eight project-owned containers, independently of Compose parallel flags.
No host protection or dependency settings were changed.

[Retained latest-run measurements](/home/lionheart/workspace/skyseed-local/experiments/phase4b-1-20260917T144814Z/retained/five-shell-20260910/qualification/latest-run/) preserve actual timing,
audits, frozen source identity and only the necessary differing source files.
Whole starting/delivery snapshots and previous normal-output copies were removed.
The initial generation with only 18 selected goals was stopped before deployment;
its raw bundle is deleted and its failure is not relabelled success. The accepted
selection correction scans the two chosen controls using authoritative directional
paths and request cadence. It does not credit B0 as a transition or invent a stable
whole-horizon control. Attachment changes retain their interval boundaries.

All 121 formal allocations passed the identical-input frozen solver comparison;
54 had different assignments at identical values of all five objectives. The warm
allocation was identical. At B0, 15 reserved pairs are exchanged (five already
operational); continuation carries those choices into later preparations and routes.
The original per-frame classification (raw file deleted; retained summary in
`formal-reference-comparison.json`) established that ISL,
Zone and common physical values stay identical; 19,032 Route outcome differences
are summed across the 121 frames. Frozen cold Route replay checked 531,426 demands,
and unchanged Compiler replay checked every boundary. These are explicit equal-cost
ties and their continuity cascade, not byte-identical old/new formal networks.


The first complete attempt used fingerprint
`def0bef4a94ab71073b725dc5246e2904cd56479f8863146d13f293d0b0181dd`.
Runtime `20260909T192308.900722200Z-3654937` remains **FAILED**: Main completed
121 timestamps, but the initial Traffic status RPC timed out at two seconds.
Packet telemetry had 4412 observations, 573 transactions and 572 endpoint
deliveries; executor completion and planned 20-goal coverage were not established.
The failed output, Tool, sealed source and temporary diagnostics were deleted at
accepted closure; this summary does not imply their continued availability.

Flag-only cleanup also failed. Compose 5.3.1 recorded 4792 stop progress rows,
1707 removal starts and zero completed removals. Its service traversal and
per-service removal groups did not enforce the requested bound
([versioned upstream source](https://github.com/docker/compose/blob/v5.3.1/pkg/compose/down.go)).
Historical diagnostics matched 6417 remaining shims to saved prior/current
container identities. Docker object absence alone had not proved shim absence.

The adopted private correction uses immutable-ID batches, reverse dependencies,
configured graceful stop/hooks, project orphan cleanup and external-network
preservation. A failure stops subsequent batches; retries inventory current owned
resources. Traffic control retries once with 2 s then 8 s RPC deadlines, records
timeouts, rejects identity mismatches and serializes worker cleanup.

On 2026-09-10 the user restarted the services. Containerd needed two 90-second
recovery retries before booting at 03:25:55 UTC; Docker was ready at 03:26:33 UTC.
No timeout/protection setting changed. The corrected cleanup removed 3058 owned
Docker objects in 238.582 s, with at most eight per batch and no errors. Historical
absence checks covered original container IDs, helper/BPF pin, project networks
and saved shim identities. This earlier manual recovery was not default-path
qualification. The subsequent successful run has the distinct identity below.


### Final fresh run and explicitly requested manual acceleration

Final-source fingerprint `641359dbff07ebb50e8e03a4de437f59022fe2b3f24650cf215478e2c1b986a4` produced
runtime `20260910T044347.546519766Z-154590`. All 121 timestamps, one foundation/B0,
120 Main activations, successor readiness, container/helper continuity and 19922
retained seam endpoints pass. Runner cleanup and all Tools complete with run.sh
exit 0. All 20 eligible Traffic coverage goals are selected and observed; health
remains WARN with 12 unexplained reachable losses and 11 unclassified executor
requests. Raw packet evidence remains independent and complete.

After confirming the saved Runner/Traffic/Tool evidence, the user explicitly asked
to finish the current cleanup batch, stop the existing loop, and force-remove only
this experiment's remaining disposable containers in small dependency-ordered
batches. The last in-flight docker rm finished with exit status 0 before its
parent loop was terminated; no next batch was started. The parent had been paused
while that removal child finished, so its final batch record was not published.
`manual-teardown-handoff.json` preserves those eight IDs and the exact process
handoff without rewriting the default log. Default cleanup therefore removed
3104 containers (387 recorded batches plus that completed handoff batch), taking
4661.343 s up to the manual handoff. Docker records 3104 ten-second grace expiries;
stop commands account for 4315.667 s. The remaining cost is measured, not inferred
from work-count sums.

The separate manual command force-removed 1661 containers in 209 batches of at
most eight and removed one owned network, taking 196.751 s outer. A process-local
command/log adapter reused the unchanged private controller's ownership, reverse
dependency, batch/failure and external-network rules. No generated control, launcher
source, general stop behavior, host protection or dependency was changed. Runtime
outer exit 241 records the requested termination of the cleanup parent, while
Runner and Traffic remain completed. This is **manually accelerated teardown**,
not successful qualification of uninterrupted defaults; no experiment was rerun.

Absence verification checks all 4765 current container IDs, project networks,
helper/BPF pin and shims against 14296 saved immutable identities. It passes.
Current normal output, browser navigation and the external network/Traffic export
are checked. Final data and separate memory/timing scopes are in the performance
presentation and `performance-measurements.json`. The nominal ten-second sampler
has an explicit handoff gap between original and manual command scopes. Original
failed attempts, strict comparison discrepancies, unclassified health observations
and the manual-default qualification limitation remain visible. Final code passed
860 offline tests (two skipped), import and documentation checks; the cumulative
milestone is now accepted by the user and Work, with backup submission authorized.


The accepted final presentation uses stable typed Zone–GS pairs and independent
recorded directional outcomes, plus episode/probe Traffic replay across segments.
Source identity for these offline Tools remains separate from frozen runtime source.
Startup is 64/eight at every count; subsequent 8/16-ID cleanup and explicit shutdown
hooks remain bounded but have no new full-scale qualification. The successful
runtime's manually accelerated teardown and Traffic WARN are unchanged.

Global unique Zone service and shared GS admission may choose incompatible,
disconnected Shell components. This is deferred Model coordination work. Per-Shell
serving/routing contexts over shared GS entities and Zone geometry are only a recorded
alternative; they are not implemented and must not duplicate shared physical GS
capacity. No algorithm, Model, topology or architecture change is part of closure.


## Whole-Shell deployment and incremental preparation (2026-09-13)

Approved scope: whole metadata-derived groups under the existing Compose owner;
one fixed startup concurrency of 8 for this scenario; one native Docker CLI
removal queue per reverse group; configuration/image/container reconciliation on
explicit deployment retry. This replaces the historical count-based strategy
above. It introduces no Runner, clock, routing or backend owner. Docker CLI
29.6.2's `parallelOperation` has a native limit of 50:
[versioned source](https://raw.githubusercontent.com/docker/cli/v29.6.2/cli/command/container/utils.go).
It must not be conflated with Compose stop/down concurrency.

The preparation representation now carries reference-counted destination deltas
through derived node routes, decodes and consumes a boundary before requesting
the next, and shares static interface identity and unchanged node route projections
across telemetry generations. READY still requires the complete admitted delivery;
Main's phased compatibility, immutable requests, B0 and fixed origin remain.
Capacity factor stays 2; the five-Shell Example changes lead fraction to 1.0 so the
first successor starts preparation at Main origin. The prior 232.446-second
construction/prewrite measurement excluded about 75 seconds of decode work.

The combined change has no claimed per-mechanism speedup. Initial focused checks
cover full-state equivalence, interleaved consumption, partial re-entry, resource
ownership, dependencies/hooks, kernel results and existing Traffic evidence.
Live evidence and exact status are tracked in [CURRENT](../CURRENT.md) and
`/home/lionheart/workspace/skyseed-local/experiments/shell-lifecycle-20260913/`.
The original campaign, paper candidates, presentations and normal outputs were
untouched by that implementation. The later user-authorized
[material cleanup](work-artifact-cleanup-20260914.json) retains unique evidence and
final outputs while removing superseded generated material. The single live
lifecycle qualification completed on 2026-09-13/14:
startup script 646.859 seconds, Preparation/B0 337.129 seconds, successor total
239.537 seconds with 60.458 seconds READY margin, and all 121 boundaries through
600 seconds. Full Runner cleanup took 51.358 seconds; generated deployment
cleanup took 306.662 seconds. An external verifier's unprivileged pin-root stat
failed; the original FAILED/UNPROVEN records remain. A read-only follow-up verified
the exact pin root absent, with all owned resources confirmed absent 392.405
seconds from the original cleanup start. Neither removal nor live execution was
repeated.

Generation/startup/run totals including their verification and required Tools were
2220.532/663.686/1479.475 seconds, below their overall ceilings. Traffic remains
WARN / coverage NOT PROVEN because S5's Zone-to-GS transition case has no observed
request. Telemetry is complete with no losses, but two replay observations remain
unclassified. Raw executor loss is 12/1345; no infrastructure or persistent-flow
failure was reported. This is lifecycle completion, not full Traffic coverage or
independent acceptance. Exact comparisons, measurements, test logs and the
verifier limitation are in the external `REPORT.md` and [CURRENT](../CURRENT.md).

<a id="offline-preparation-closeout"></a>
## Retained IPv4 inventory and direct v4 decode (2026-09-14)

These adopted private preparation mechanisms follow the full-scale run above.
Their evidence is offline/read-only; that earlier run did not execute these later
changes. Evidence root:
`/home/lionheart/workspace/skyseed-local/experiments/offline-closeout-20260914/`.
Successful/interrupted run bundles and normal outputs are preserved unchanged.

**Owner and mechanism.** The existing BPF
`_RouteSocket` (historical source: `../../src/skyseed_v2/backend/bpf/_runner/kernel_netlink.py`)
now performs the initialization IPv4 inventory via `RTM_GETADDR` and all-table
`RTM_GETROUTE` dumps. The existing namespace owner retains/closes the socket;
`_audit_node_addresses` still verifies the namespace incarnation before inventory,
rejects address/route collisions and propagates failures into initialization cleanup.
`IFA_LOCAL` takes precedence over the peer `IFA_ADDRESS`; default routes are
excluded, local/broadcast and other tables remain. Multipart reads wait for DONE,
not merely a successful ACK, and reject interrupted/truncated/error results. These
protocol distinctions follow the [Linux Netlink documentation](https://docs.kernel.org/userspace-api/netlink/intro.html)
and [IPv4 address UAPI](https://raw.githubusercontent.com/torvalds/linux/master/include/uapi/linux/if_addr.h).
No CLI fallback or concurrency change was added.

The single allowed read-only current-namespace comparison found identical multisets
of three local address/prefix facts and nine non-default route networks. Netlink
took 0.000618 seconds and the two `ip -j` commands took 0.266398 seconds; the
namespace inode stayed 4026531833. Raw facts and timing are in
`netlink-readonly-comparison.json`, `ip-addresses.json` and `ip-routes.json`.
Removing two subprocess launches per node explains the intended work reduction,
but neither that sample nor the historical 72.060-second initialization audit
measures the new full-scale audit cost.

**Decoder diagnosis and choice.** The original
`_runner_artifact.py` (historical source: `../../src/skyseed_v2/backend/bpf/_runner_artifact.py`)
is frozen externally as `baseline_runner_artifact.py`. Representative boundaries
0/30/60 and a boundary-30 profile identified the recursive generic operations tree
as the largest decode component: boundary 30 spent 0.007530 seconds on slice/hash,
0.812603 on generic unpack and 0.293924 on typed reconstruction. The adopted
`_PackedBoundaryReader` reads the same v4 tags with a checked byte cursor and
directly constructs typed operations. It keeps generic decoding only for metadata,
validates canonical keys/lengths/tags/UTF-8 and uses the existing validated intent
constructors. File/index/record hashes and exact operation order are preserved.
There is no writer, delivery API, format, READY or clock change.

Short text (at most 256 bytes) and scoped target caches each stop growing at 16384
entries. Node/connection/time reuse stays local to one finite artifact; all caches
clear on close. The matched successor used 8376 texts and 3494 targets, plus the
existing 4763 node/13120 connection/61 timestamp identities. No decoded history,
lazy intent wrappers or new dependency is retained. This accepts bounded reuse
memory for less repeated conversion, within the existing artifact lifecycle.

**One matched comparison.** `matched_decode.py` runs separate before/after processes
on the identical existing `runner-artifact-001/manifest.json` and 481043017-byte
binary (61 boundaries, 732789 operations). The manifest SHA-256 is
`dedaa0e65e8643938dd42f9f5e359614f2464c7c1e33b4e68dc2075ddbae1cd9`.
Decoder source hashes are recorded in `before.json` and `after.json`. Both use
Conda `skyseed_dev`, `CONDA_PREFIX=/home/lionheart/miniconda3/envs/skyseed_dev`,
Python `/home/lionheart/miniconda3/envs/skyseed_dev/bin/python` 3.12.13 and pip 26.1.2.
Identical CPU-only predecessor setup consumes the first delivery and retains its
terminal state/build, taking 79.064/77.203 seconds outside the measured successor.
No kernel executor or resource mutation is used.

| Successor scope | Before | After |
|---|---:|---:|
| Loading/validation | 13.374 s | 12.992 s |
| Complete boundary decode | 99.138 s | 42.706 s |
| Actual route derivation and activation-request construction | 33.650 s | 34.572 s |
| Separate semantic/derived proof work | 57.080 s | 50.362 s |
| Peak process RSS (`ru_maxrss`) | 829607936 bytes | 788738048 bytes |

Each decoded boundary re-encodes to its sealed record digest; all 61 before/after
derived activation, generation-node and connection digests match. Only current
boundary scratch and terminal predecessor state are retained, not two decoded
batches. All identity caches were verified empty after close. Peak RSS includes
setup, mapped file pages, construction and proof scratch; it is not incremental
decoder allocation or deployment-wide container memory. Decoding decreases 56.9%;
construction increases 0.922 seconds (2.7%), while their combined cost decreases
41.8%. Eager reconstruction and this downstream measurement support an actual work
reduction rather than deferred conversion. This single comparison excludes kernel
prewrite, telemetry assembly and Main/channel contention, so it establishes neither
a new live READY margin nor full-scale preparation time/memory.

The accompanying cross-group namespace retry correction reconciles actual owner
IDs and transitive dependents before recreation, retaining real stop hooks/grace,
identity/deadline checks and failure evidence. It is a correctness fix under the
Compose deployment owner, not another optimization item or active Runner recovery.
S5's missing transition observation is accepted sparse coverage; the original WARN
and unexplained losses remain. This closes the authorized round without another
live qualification, tuning campaign, host change or dependency installation.

Final affected regression suite: 428 passed, one existing single-realization
fixture skip in 52.90 seconds, including functional replay using the already
installed VS Code Node executable. Canonical environment, import dependencies,
full documentation and whitespace checks pass; exact scope and logs are recorded
in [CURRENT](../CURRENT.md) and the separate offline evidence root. Earlier focused
tests overlap this suite and are not summed into an inflated total.

Phase 1 path synchronization: current implementation links point to default
assembly and the shared workflow. Historical measurements and checkpoint identities
in this document still describe their original sources.
