# 已采用的优化与宿主机配置选择

本文解释已采用机制的工作方式和代价，方便阅读当前代码；它不是新的架构
权威、配置接口或优化提案。正式合同见 [accepted design](../accepted-design.md)，
阶段状态见 [CURRENT](../CURRENT.md)。源码定位、Git 检查点和证据边界见
[详细决策历史](../provenance/optimization-decision-history.md)。

## 计算：减少重复求解，保留精确语义

**GS/Zone 空间筛选。** 将地面站和 Zone 的静态 ECEF 坐标放入均匀网格
（uniform grid），按卫星位置与保守距离上界查询附近格子，再做精确几何
检查。这样减少进入昂贵几何计算的候选；索引必须保留所有可能合格的对象，
不能近似替代仰角等资格判断。它不是 KD-tree。
[记录 C1](../provenance/optimization-decision-history.md#c1)

**按终点共享路由搜索。** 同一类目的终点共享一次 reverse BFS，从终点
反向计算最少跳数，再为实际请求的源节点展开所需分支。多个源复用搜索结果，
不必各自搜索整张图；仍保留合法中转节点、确定性选路和明确的不可达结果。
这是 hop-count 搜索，不是按权重求最短路的 Dijkstra。
[记录 C2](../provenance/optimization-decision-history.md#c2)

**有界路由进程池。** 较大的路由任务分组交给可复用的 `ProcessPoolExecutor`，
按图规模、任务数和可用 CPU 选择并发数，避免每帧重新创建进程。小任务直接
执行，较大任务最多使用 8 个进程；进程通信和内存仍有成本。它由 Planner
的 Route Model 管理，与 Runner 删除 veth 的线程池无关。
[记录 C3](../provenance/optimization-decision-history.md#c3)

**稀疏、有容量约束的 GS 分配。** 将可行 GS–Satellite 候选映射为整数索引，
用候选邻接表、占用列表和数组表示二部残量图，再执行 successive shortest
paths。选择这种表示是为了保留稀疏性，而非引入新分配策略：最大匹配数、
候选排名、Satellite 顺序及同代价解的确定性都必须保持。
[记录 C4](../provenance/optimization-decision-history.md#c4)

**累积死区参数规划。** 延迟漂移相对“上次生效值”计算，减少小幅变化造成的
有效参数更新。canonical 场景选择 191 μs deadband、10 秒 persistence、
30 秒 maximum staleness：超过死区后，候选延迟值需保持不变达到 persistence；
达到 staleness 则可直接采用当前值。这允许物理值与有效值暂时不同，不能
保证误差始终小于 191 μs；该数字的精确选值理由未恢复。
[记录 C5](../provenance/optimization-decision-history.md#c5)

## Compiler 与持久化：用合适的表示保留事实

**折叠已由执行顺序保证的 precedence。** Compiler 将已有 activation
frontier、Runner phase 或 atomic forwarding generation 已保证的先后关系
记录为折叠证明；其余关系保留精确约束。这样避免把同一顺序展开为大量边，
同时不放宽 Planner 的语义要求。
[记录 P1](../provenance/optimization-decision-history.md#p1)

**紧凑、流式的语义证据。** Planning evidence 用索引表示 precedence 端点，
每次 Route transition 只编码一份 RouteDemand，并使用低压缩级别的 gzip
JSONL。Compiler inspection 保留需要的摘要与边界工作量，避免复制完整运行
载荷。代价是读者必须遵循各自 schema；紧凑 Compiler history 不能当作完整
Runner 输入。旧离线测量与当前 artifact-v3 的记录分开保留。
[记录 P2](../provenance/optimization-decision-history.md#p2)

## Runner：提前准备，完整执行当前边界

**有限预计算与 prepared deltas。** 生成阶段完成全部编译并封存 artifact；
Preparation 解码完整序列、构造 delta 和 wire command，并实际激活 B0。
正式运行从 B1 开始，Main 只执行当前已准备的变化。计算被移到运行前，代价
是完整有限计划占用内存；Maintenance、READY 和运行时 generation reclaim
已移除。固定原点计时是执行约束，不单列为性能成果。
[记录 R1](../provenance/optimization-decision-history.md#r1)

**逻辑端口与完整 generation 预写入。** BPF semantic maps 使用稳定 logical
port，少量 binding maps 再将其映射到 live ifindex，因此未来 generation
可以预写入，而未来物理接口仍等到自己的边界才创建。发布 generation 只是
转发选择子阶段；退出旧状态和物理删除完成后，边界才成为 formal ACTIVE。
这些都是后端表示，不新增领域 Port；全量语义表驻留到 cleanup。
[记录 R2](../provenance/optimization-decision-history.md#r2)

**持久特权执行通道。** 现有 helper 在用户态接收已编码命令，复用 namespace
rtnetlink sockets 和 BPF descriptors，直接提交系统调用，减少逐动作启动
子进程的成本。它不是把全部指令永久放到内核里执行；保留的身份和资源失效
仍会导致失败，cleanup 负责关闭它们。
[记录 R3](../provenance/optimization-decision-history.md#r3)

**分组删除仍在 Main 内完成。** Preparation 为每个连接生命周期安排
retirement cohort；helper 按 namespace 发出 `RTM_DELLINK`，由 grouped
netdevice unregister 删除 UP veth 及隐含 peer。有限线程重叠独立 namespace
的等待，但当轮所有任务必须 join；helper 自有 ledger 保留清理责任，不形成
跨边界删除服务。候选 rollback 仍按单对删除。
[记录 R4](../provenance/optimization-decision-history.md#r4)

**创建时一次携带接口属性。** 每个端点显式配置一个 RX/TX queue，并在 veth
创建请求中携带 MAC 与 group，避免额外属性设置往返。对应内核属性为
`IFLA_NUM_RX_QUEUES`、`IFLA_NUM_TX_QUEUES`、`IFLA_GROUP`。这采用当前场景的
单队列选择；现有联合测量不能拆出它独立贡献的加速倍数。
[记录 R5](../provenance/optimization-decision-history.md#r5)

**观测写入移出 Main。** 紧凑事件通过非阻塞队列交给 writer，接口身份由后台
writer 按预备模板和 binding delta 展开，减少主执行路径的序列化和 I/O。
写入仍消耗资源，结束时必须 drain；事件队列溢出或写入失败必须保留为证据
不完整，不能伪装成功。它们属于既有 observation facilities。
[记录 R6](../provenance/optimization-decision-history.md#r6)

## Tools 与部署：不同的并发、不同的责任

**Detached Tools。** 检查工具在独立进程中读取冻结输入并生成展示，避免把
渲染变成领域算法的一部分。进程数有界，提交满额时会等待，最终也要 join；
detached 不意味着零开销或每次 submit 都非阻塞。
[记录 D1](../provenance/optimization-decision-history.md#d1)

**相同 build context 只构建一次。** Starlink Compiler 输出按相对文件名和
字节内容分组，每组构建代表镜像，再复用 image tag，保留各 service 的部署
身份。只有完全相同的生成内容才能共享；不能把内容相近的节点合并。Mini
当前仍使用自身的普通 build 启动路径。
[记录 D2](../provenance/optimization-decision-history.md#d2)

**单 Compose project 与启动并发。** 可选程序式 launcher 默认一次启动完整
项目，并显式限制 `--parallel 16`。当前 canonical 生成脚本同样使用一个项目，
但没有固定 `--parallel` 或 `COMPOSE_PARALLEL_LIMIT`，不能套用 launcher 的
数值。Runner lifecycle 与部署启动仍分离；旧 Compose/BuildKit 兼容试验不是
当前运行要求。
[记录 D3](../provenance/optimization-decision-history.md#d3)

## 这台宿主机：控制面容量与存储放置

**D-Bus 与 inotify 限额。** 大量容器启动曾触发连接和监听实例耗尽，因此本机
分别将每用户 D-Bus connection 配置从 256 调至 512，将 inotify instances
从 128 调至 1024，换取更多控制面余量，也允许更多资源占用。前者仅有配置
文件观察，不能当作本次查询了 bus 内部有效上限。
[记录 H1/H2](../provenance/optimization-decision-history.md#h1)

**Docker 使用 cgroupfs。** 历史上逐容器创建 systemd transient scope 出现
超时；本机选择 `native.cgroupdriver=cgroupfs`，由 Docker/runc 直接管理容器
cgroup，仍使用 cgroup v2。代价是减少逐容器 systemd scope 的统一管理与可见性。
回退只应调整相关配置，保留 `daemon.json` 的其他键。
[记录 H3](../provenance/optimization-decision-history.md#h3)

**NVMe 放置 Docker/containerd 与工作目录。** 本机通过 `/srv/skyseed-hot`
及 bind mounts 保留原访问路径，并用 systemd mount dependencies 保证服务
依赖的存储先就绪。这是存储布局选择，不能归因于 D-Bus、inotify 或 Runner
描述符预留；其独立性能收益没有对照证据。路径和设备归本机记录，不能复制
成通用安装方案。
[记录 H4](../provenance/optimization-decision-history.md#h4)

## 历史与收益边界

旧 rich-realization handoff、Runner 的旧 generation 生命周期以及串行逐对
退休方式均有历史记录，但已被当前机制替代。未采用试验不列为当前选择；
普通 bug fix、重复扫描删除和小型防御检查不单列为成果。Runner 联合变化的
S3 数值沿用 [原里程碑](../provenance/runner-optimization-20260907.md)，
不为每一项虚构独立收益；历史 Traffic WARN 保持原分类。
