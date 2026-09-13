# 已采用的优化与宿主机配置选择

本文解释已采用机制的工作方式和代价，方便阅读当前代码；它不是新的架构
权威、配置接口或优化提案。正式合同见 [accepted design](../accepted-design.md)，
阶段状态见 [CURRENT](../CURRENT.md)。源码定位、Git 检查点和证据边界见
[详细决策历史](../provenance/optimization-decision-history.md)。

## 计算：减少重复求解，保留精确语义

**物理批计算与接触证明。** 当前圆轨道路径复用轨道常量、地面坐标与 Zone
几何，以有界数组块进行距离筛选，再做精确仰角和椭球 LOS 检查。未来查询仅
保存紧凑的连续可用区间；可证明稳定的 ISL 使用全相位上界，其余区间用运动
界保守证明。不能证明时保留 unknown，不把预测截止点当作断链。原有空间
索引是起点，保留为受限查询/参考算法，不把已有机制重新计作本轮成果。
[记录 C1](../provenance/optimization-decision-history.md#c1)；
[本轮记录 C6](../provenance/optimization-decision-history.md#c6)

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

**覆盖与容量利用的 GS 分配。** 当前稀疏整数残量图依次最大化有分配的 GS 数、
不同接入卫星数和总连接数，再最小化有效分配变化，最后比较仰角排名与确定性
平局。准备中的预留占用两端容量并保留连续性价值，但尚未提供服务。只有保留
集合同时达到前三项目标的独立上界时才能直接复用；仅证明 GS 覆盖不增加
已不足够。新候选仍需未来服务证明，满端口替换仍付出准备空窗。
Example 的 GS 未显式配置能力时假定四路，显式设备能力与可选策略上限分开；
卫星端两路默认值保持。目标只针对已准入查询图，不宣称完整物理图最优。
[历史 C4](../provenance/optimization-decision-history.md#c4)；
[本轮记录 C6](../provenance/optimization-decision-history.md#c6)

**相对延迟阈值与最长刷新。** 延迟相对上次生效值累计变化，达到 10%（含
等号）就在当前采样采用并重置基线；零基线、下降方向均有确定规则。首次达到
120 秒的采样刷新当前值；值不变时只更新记忆，不发无意义事件。原 191 μs /
10 秒 persistence / 30 秒 staleness 是 C5 中的历史策略，本轮不再使用。
[历史 C5](../provenance/optimization-decision-history.md#c5)；
[本轮记录 C6](../provenance/optimization-decision-history.md#c6)

**有限批次和有界流水。** 32 帧是内部起点，不是新的控制时间窗；取消独立
50 秒等待，保留每个正式时间戳。准备历史与未来尾部由 5 秒准备和 180/60 秒
有效服务依赖推导。证据串行写线程和 artifact/Traffic 批处理各有队列上限；
语义状态仍按时间推进。路由整数图、终点分组和已有进程池继续复用，spawn
避免继承活动写线程。材料封存后先发布 ready，再收齐有限 Tool 结果。
[本轮记录 C7](../provenance/optimization-decision-history.md#c7)

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
Runner 输入。旧离线测量与优化里程碑的 artifact-v3 记录分开保留；当前 scoped-service 合同使用 artifact-v4，不重新解释旧记录。
[记录 P2](../provenance/optimization-decision-history.md#p2)

## 最终优化轮：减少重复工作，保留完整事实

GS allocator 的精确整数目标同时奖励 GS 覆盖、不同卫星覆盖与连接总数；
旧 incumbent 前缀不再预先固定。保留解证明失败时求解当前稀疏残量图。
GS 候选从 32 开始，在容量竞争或不足时扩展，并包含有效准备/运行连接。
参考 ISL 使用权威循环轨内前后邻星和 K=1 邻轨，每次查询重新选择邻轨近邻；
较宽范围仍可配置。几何仍在所查询 K 范围和 GS 物理包络内计算，减少候选对象
不等于省去全部几何。Route engine 只重算依赖发生变化的当前请求，GS 接入
变化不会自动使所有目的地失效；确定性路由和必要替换仍保留。

受控实验确认 Python 线程交接会放大数组计算的 wall time；物理 Model 改用
较大的有界 tile 并减少候选重复校验，artifact/Preparation 则使用增量索引。
容量按共享目的地址的引用计数，避免为了计数反复重建完整路由。仍保留
流水重叠与必要的有限准备，不改变 Main 或 cleanup 的责任。

Planner/Traffic Tools 合并重复重放与转换，Runner Tool 集中一次输入隔离，
文件 inventory/checksums 复用同一 digest。完整候选、边界、Traffic 与失败
证据继续保留。当前实现和本轮 qualification 仍待独立审核；机制、定向
测量范围及限制见 [记录 C8](../provenance/optimization-decision-history.md#c8)。

## Runner：提前准备，完整执行当前边界

**有限预计算与 prepared deltas。** 生成阶段完成全部编译并封存 artifact；
单交付 Preparation 解码完整序列、构造 delta 和 wire command，并实际激活 B0。
正式运行从 B1 开始，Main 只执行当前已准备的变化。计算被移到运行前，代价
是完整有限计划占用内存。该历史 one-shot 选择由本轮已批准的连续有限交付
扩展：首批 Preparation，后继 Maintenance、READY 截止点与安全回收；单交付
仍保留原有有限行为。固定原点计时是执行约束，不单列为性能成果。
[记录 R1](../provenance/optimization-decision-history.md#r1)

**逻辑端口与完整 generation 预写入。** BPF semantic maps 使用稳定 logical
port，少量 binding maps 再将其映射到 live ifindex，因此未来 generation
可以预写入，而未来物理接口仍等到自己的边界才创建。发布 generation 只是
转发选择子阶段；退出旧状态和物理删除完成后，边界才成为 formal ACTIVE。
这些都是后端表示，不新增领域 Port；单交付语义留到 Cleanup，连续模式
复用相同 node version 并安全回收无在途 reader/引用的已退役内容。
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

**相同 build context 共享一个镜像。** Starlink Compiler 输出按相对路径、
内容与模式分组，每组保留一个 build，并让组内 service 直接引用同一个项目
镜像。节点自己的 container、mount、environment 和身份不合并。启动只检查
唯一镜像集合与 project label，不再为每个 service 创建和检查等价 image tag。
Mini 仍使用普通 build 启动路径。此前仅共享 build、随后复制 tags 的机制保留
为历史 D2；新实现与证据见 [记录 D4](../provenance/optimization-decision-history.md#d4)。

**按实际消费读取与分块写 JSON。** Traffic 在 sealed planning history 中读取
Plan 语义和 lineage，单次重放后复用已经验证的投影输入。它不重建未展示的
Model inspection 对象；这些原始证据仍由 Planner Tool 完整消费。公开的可变
snapshot 投影继续检查语义。Traffic JSON 保留全部字段，以单个 record 为单位
紧凑编码、流式写入和 hash；移除整个大文件的重复字符串/字节副本，HTML 与
评分不变。Planner 的 payload hash 同样流式读取。
[记录 D5](../provenance/optimization-decision-history.md#d5)

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

本轮的条件、计时、真实重叠与资源边界集中维护在
[三条件性能视图](qualification-performance.md)；从配置到真实转发的因果链见
[Framework 构建与执行图](framework-construction-execution.md)。

## 连续有限交付与进一步复用（2026-09-08，待 Work review）

本轮保留逐时物理事实与所有正式边界，在当前候选形状和容量/策略约束能证明
保留集合不变时省去 ISL policy sort；证据继续使用当前候选。GS 直接使用
候选值，精确保留分配成立时不再构建 satellite index。Route 复用未变的
demand/request/outcome，只在 assignment/demand/graph 依赖改变时失效。
Physical World Tool 的内部 owned snapshot 路径移除会被覆盖的全帧 deepcopy；
公开 projection 仍隔离输入。

Runner 构造复用未变的每节点 route/access 字典和 generation-node records，
静态 demand index 每批只构建一次。相同 node 内容跨 generation 共享私有
version，减少真实 BPF entries 写入；无需替换现有 tc dataplane。更深的全局
表格替换未采用，因为当前局部版本共享已能独立验证 publication 与引用回收，
全局替换会改变更多转发/回收语义，超出本次可收敛的工作。

连续交付将后继构造移入运行期间；这一移动不等于减少总工作。Runner 的
Maintenance 以有界 helper 请求、Main 优先权和 deadline 限制执行。固定节点、
append-only lineage、一次 B0/clock，以及在途 readers 和引用安全回收共同
限定此模式。能力和代价详见 [正式合同](../accepted-design.md#consecutive-finite-delivery)
与 [本轮决策记录](../provenance/optimization-decision-history.md#b1)。
实际比较与未证明范围以 [性能页](qualification-performance.md) 为准。

## 最后集中修复：依赖复用与有界收尾

GS 扩展先判断候选耗尽，再做无质量费用的必要条件/可行匹配；相邻帧复用
已耗尽查询的数量提示，每帧仍重新求物理与准入事实。精确分配从合法保留边
开始，用残量负环消除处理增补和交换，无负环即全五层整数目标的最优证明。
同帧物理查询共享 ISL/Zone 结果、GS 接触筛选分组及已构造对象；失效依赖
包括时间、位置值、world、预测、查询范围与保留关系。统计区分 rank 省略与
接触时长拒绝，computed/reused 另记，不把事实候选数当实际重算数。

路由按源可达卫星连通分量的完整结构和 GS 接入失效，保留无关分量缓存，
支持新短路、合并、分裂和多接入。请求构造按 demand/terminal/index 复用。
Traffic 共享接口身份和代际区间，保留旧 JSONL 的流式读取；两条长流仅在
实际 eligible window 内抵扣覆盖目标。正常 cleanup.sh 按依赖逆序显式分批，
每批最多停止并移除 8 个已核实项目归属的容器。Compose 5.3.1 的 down 不会
把并发参数用于全部 service 停止，因此不能只设置该参数。Traffic 控制 RPC
超时仅重试一次并保留证据，身份异常或再次超时仍失败；清理由 worker 完成。
机制细节、测量局限见[本轮记录](../provenance/optimization-decision-history.md#focused-correction)。


2026-09-13 的部署策略以整个 Shell 为进度和失败定位单位：共享 GS 先启动，
然后依次提交完整 Shell，启动并发固定为 8；退出逆序，每次只提交一个组，
复用 Docker CLI 自带的 50 操作队列。真实依赖和明确退出语义仍优先。
部署失败保留已确认且配置匹配的组；显式重试还核对跨组 namespace 的实际
owner 容器 ID。owner 缺失、失败或已替换时，先逆依赖移除受影响的传递使用者，
再按组重建；无关健康容器保留。这是重试一致性修正，不单列为性能成果。
此前按容器数量分段的策略已被替代，不再拟合宿主最佳参数。

准备路径把引用计数的目的地址变化直接传到节点路由，并逐边界解码、构造和
释放临时数据；遥测复用静态接口身份和未变化节点的路由描述。首批和后继
使用同一路径，只有整个交付准备及预写成功才可 READY。此五 Shell 例子的
提前比例为 1.0，容量倍率仍为 2，保持一个 Runner 和固定时钟。
实测状态与证据边界见 [采用记录](../provenance/optimization-decision-history.md#whole-shell-deployment-and-incremental-preparation-2026-09-13)。

**直接恢复 v4 边界。** 2026-09-14 的离线诊断确认通用递归解包占主要处理
时间。私有 reader 直接从原 v4 字节游标构造 typed operations，省去每条操作的
通用 list/dict 中间树；元数据仍按规范读取。短字符串和带 scope 的目标各最多
复用 16384 项，节点、连接、时间身份沿用单个有限 artifact 的缓存，关闭时全部
清空。每次只恢复当前边界，所有 intent 均立即构造，未把转换推迟到下游；整文件
及逐记录 hash、格式检查、顺序和完整 READY 保持。缓存需要有限额外内存，不能
把离线进程 RSS 当成全容器部署的总用量或新的 live READY 余量。

**复用 namespace 的 IPv4 查询通道。** 首次碰撞审计使用初始化已经持有的
Netlink socket 读取本地地址/前缀及所有路由表的非 default 路由，省去每节点
两个 `nsenter/ip` 进程。仍先核对 namespace incarnation，区分点对点接口的
local 与 peer，收齐 multipart dump 并传播内核错误。原有审计、并发和失败清理
责任保持。仅完成一次当前 namespace 的只读语义核对，未测新全规模审计时长。
两项采用机制的匹配测量及限制见 [离线收尾记录](../provenance/optimization-decision-history.md#offline-preparation-closeout)。
