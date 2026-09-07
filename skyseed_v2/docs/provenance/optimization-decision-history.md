# 已采用优化与宿主机配置的决策历史

本记录保存已采用机制、可追溯源码和现有证据的边界，不建立新的架构权威、
优化方案、配置接口或运行组件。人类入口是 [中文机制说明](../human-readable/optimization-decisions.md)；
当前合同以 [accepted design](../accepted-design.md) 为准，阶段状态只见
[CURRENT](../CURRENT.md)。本记录没有重新运行生成、部署或性能测量。

## 证据读法与检查点

- **当前源码事实**：本记录的当前实现指 `6e8652a`。源码链接指向仓库文件，
  符号名用于定位；将来代码变化时，可用该 Git 检查点恢复本文所述版本。
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
[源码](../../src/skyseed_v2/simulator/models/_geometric_physical.py)：
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
[`_routing_core.py`](../../src/skyseed_v2/simulator/_routing_core.py) 和
[`_route_computation.py`](../../src/skyseed_v2/simulator/_route_computation.py)，
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
[`_ground_station_allocation.py`](../../src/skyseed_v2/simulator/_ground_station_allocation.py)，
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

**Owner / scope。** 公共 `AccumulatedDeadbandParameterPlanningModel` 与
`ParameterKindConfiguration` 位于
[`planning/models.py`](../../src/skyseed_v2/simulator/planning/models.py)，
Planner 拥有有效状态和 memory；场景注入见
[`Starlink simu.py`](../../examples/starlink_gen1_satellite_internet/simu.py)。

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
[`_compile_constraints`](../../src/skyseed_v2/backend/bpf/compiler.py)，在
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
[`planning/evidence.py`](../../src/skyseed_v2/simulator/planning/evidence.py) 的
`plan_to_data` / `plan_transition_to_data`；机械流读写位于
[`_durable_evidence._open_jsonl`](../../src/skyseed_v2/shared/_durable_evidence.py)。
Producer 的 [`_persist_compiler_lineage` / `_compact_compiler_evidence`](../../src/skyseed_v2/experiment/_realization_producer.py)
生成 compact Compiler 记录；[Compiler Tool reader](../../tools/bpf_compiler/reader.py)
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
[`_RunnerArtifactWriter`](../../src/skyseed_v2/backend/bpf/_runner_artifact.py)、
[`BpfRunner`](../../src/skyseed_v2/backend/bpf/runner.py) 和私有
[`runtime_realization.py`](../../src/skyseed_v2/backend/bpf/_runner/runtime_realization.py)。
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
[`privileged_runtime_command.py`](../../src/skyseed_v2/backend/bpf/_runner/privileged_runtime_command.py)。
字段及 map 合同见 [private Runner README](../../src/skyseed_v2/backend/bpf/_runner/README.md)。

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

**Owner / source。** [`runner._PrivilegedHelper`](../../src/skyseed_v2/backend/bpf/runner.py)
及 [privileged command loop](../../src/skyseed_v2/backend/bpf/_runner/privileged_runtime_command.py)，
Linux 执行在 [`kernel_netlink.py`](../../src/skyseed_v2/backend/bpf/_runner/kernel_netlink.py)，
startup identity 在 [`process_incarnation.py`](../../src/skyseed_v2/backend/bpf/_runner/process_incarnation.py)。

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
[producer/runtime contract](../codex-reference/producer-runtime-contracts.md)。

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
[`_NonBlockingEventHistory`](../../src/skyseed_v2/backend/bpf/_runner/event_history.py)
与 [`_TrafficTelemetryRecorder`](../../src/skyseed_v2/backend/bpf/_runner/traffic_telemetry.py)。

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
[`tools/_detached_inspection.py`](../../tools/_detached_inspection.py) 的
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
### D2：字节相同 build-context 分组与代表镜像复用

**Owner / source。** deployment utility
[`compose/launcher.py`](../../src/skyseed_v2/compose/launcher.py) 的
`_write_compose_build_plan` / `_write_compose_project_scripts`，`fa280e0` 引入，
在 `6e8652a` 保留。当前调用见
[`Starlink main.py`](../../examples/starlink_gen1_satellite_internet/main.py)。

**机制与路径。** 对每个 context 按排序后的相对路径与文件字节计算 SHA-256，
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
旧值和原因如下。当前值/路径为本次用户提供的 Work 核验结论，不伪称本次
重新测得。H4 的存储事实单独记录；没有恢复其引入 commit 或精确批准日期。
这些选择由宿主机管理员维护，不属于 Simulator/Planner/Compiler/Runner API。

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
