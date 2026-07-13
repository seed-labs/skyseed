# 当前验证状态

这是当前代码的动态状态页。廉价套件、环境资格检查或真实运行完成后更新这里；`docs/migration/` 是结构迁移门禁的冻结记录，不写入后续测试计数或 UX/runtime 变化。

最后更新：2026-07-13。

## 标准仓库表面

当前仓库工作树只保留独立 `skyseed_v1/` 实现，以及仓库 README 和忽略规则。
v1 之前的 `satellite/` 实现、原型 `ai-code/` 和迁移期 `WORKTREE_MAP.md` 已在
切换完成后通过普通后续提交删除；其历史仍可从 Git 和全量备份 remote 找回。
这些目录物理不存在时，v1 69/69 测试及 S4/S5 直接 inspect 仍全部通过。

## 当前廉价门禁

- 完整 v1 套件：85/85 通过；
- 迁移回归套件：最近一次 792/792 通过；
- 受影响模块编译和 `git diff --check`：通过（最终提交前重验）。

新增回归覆盖 BPF no-prepare/attach-reuse 证据跨 operational-clean 会话压缩边界仍可供 fail-closed 热路径审计消费；控制台测试覆盖有界命令摘要、语义活动标签、诊断子进程过滤和重复心跳抑制，投影仍在网络更新路径之外。成本策略测试保证纯耗时异常只触发调查和后续大跑阻断，不把已经通过语义与清理契约的网络改写为失败。

新增门禁还覆盖：滚动 horizon 共享唯一物理链路时间轴、帧偏移对齐、终点帧覆盖、Compose 物化身份完全一致、可视化精确消费冻结输入，以及 worker attach handoff 压缩后仍可完整消费。

## 当前连续物理时间 S4 基线

`rich_s4_operational_clean_bpf-20260713T033419Z` 是当前语义和运行基线：

- 在硬重启后直接执行 `python examples/s4_operational_clean/run.py`；
- S4 包含 348 颗卫星、28 UT、12 GW、14 zone、四个 240 秒 horizon，10 秒采样；
- 唯一 97 帧物理链路时间轴覆盖 0--960 秒闭区间，H0/H1/H2/H3 分别消费偏移 0/24/48/72；
- 四段 topology 物化哈希完全一致，orbit/snapshot/物理时间对齐门禁均在 Compose 前通过；
- prepared-runtime consumer gate 接受 100 条记录，每个 horizon 25 条，共 1,246 个 binding ref，0 blocker；
- 100/100 runtime epoch 和三次真实 horizon 切换全部成功；
- H1/H2/H3 worker 分别耗时 63.960、61.948、60.043 秒，相对 240 秒边界保留约 176--180 秒余量；
- transition 平均 3.339 秒、P95 6.902 秒、最大 9.335 秒；data-plane-ready lag 平均 2.296 秒、P95 3.254 秒、最大 7.080 秒；没有 transition 或 ready lag 超过 10 秒；
- 返回码 0，结论 `RICH_S4_OPERATIONAL_CLEAN_PASS`；回滚、Compose teardown 和独立后置预检均为零归属残留。

worker attach handoff 报告因重复嵌套 compiler payload 达 112--194 MiB。运行后结构修复只投影不可变的接口/角色/binding 能力；使用本次成功 H1/H2/H3 产物重放后缩小到 1.51--1.67 MiB，同时保留全部接口、角色以及完整 ifindex/MAC binding cache。85/85 测试通过；没有为纯报告修改再启动长跑。

本次 runtime 墙钟下降是真实观测，但不能据此声称物理时间修正优化了 epoch 算法。早期 S4 平均每 epoch 应用 46.23 个操作，本次为 46.64 个，因此可以排除“少做了有效工作”；本次修正也没有修改 epoch 热路径。改善表现为整体长尾下降：平均 7.418→3.339 秒、P95 16.466→6.902 秒、最大值 21.320→9.335 秒。结合运行前硬重启和清理累计产物，宿主机争用与存储/I/O 尾延迟降低是目前最可信的解释。旧详细产物已按约定删除，无法事后证明唯一的组件级原因；在受控重复运行前，不把它升级成算法性能结论。

连续时间轴修复位于解析后 profile 共用的 runner，并非 S4 特判。回归测试现在同时证明规范 S4/S5 在四个 240 秒 horizon 中共享同一个物理基准时间并使用 0/24/48/72 帧偏移；S5 同样获得 fail-closed 时间对齐和 Compose 物化身份门禁。尚未用修正后的连续物理时间语义重新长跑 S5，旧 S5 仍只作为 runtime 机制证据。

## 早期多 horizon 运行的重新定性

早期 S4/S5 与结构切换运行推进了 orbit/snapshot 时间，但 successor horizon 重复消费 H0 物理链路帧。它们仍可证明 runtime 机制、BPF generation 切换、Compose、回滚、清理和结构等价性，但不能证明连续多 horizon 的物理拓扑或路径语义正确。上述 2026-07-13 运行取代它们承担这一结论。

## 早期 S5 运行机制证据

运行 `rich_s5_operational_clean_bpf-20260712T182300Z` 从示例直接 `run.py` 启动，完成四个 horizon、100/100 runtime epoch，真实应用、连接、BPF map-only 热路径、回滚和零残留均成功。随后 profile 最终化因 `normal_path_blockers` 局部变量读取早于赋值而异常退出；该报告层缺陷已修复，并用真实运行产物的离线最终化重放证明返回 0。它不否定网络运行，也未通过伪造网络结果解决。

## 早期 S4 运行机制证据

运行 `rich_s4_operational_clean_bpf-20260712T193444Z` 从示例直接 `run.py` 启动：

- 完整 S4（348 颗卫星、28 UT、12 GW、14 zone），四个 240 秒 horizon；
- 100/100 runtime epoch 成功，真实应用、BPF 复用和回滚成功；
- 后置预检 `ready=True`，无 blocker、warning 或清理建议；
- runtime epoch 平均 7.418 秒，P50 6.267 秒，P95 16.466 秒，最坏 21.320 秒；S5 平均 2.810 秒；
- S4 平均应用操作数 46.23，S5 为 19.36；耗时比约 2.64，操作量比约 2.39，支持“主要随实际工作量增长”的初步判断，但不足以证明严格线性；
- 运行主体报告成功，最终旧成本门禁仅因启动较慢及单次 `unknown_blocking_seconds=12.187` 把整次返回改为 2。

该门禁已按当前工程原则修正：纯时间异常继续完整记录、继续阻止无人值守的下一次广泛长跑，但不能脱离操作内容和语义结果改写成功。使用本次真实产物离线重算得到 2 条告警、0 条阻断；没有再启动长跑。

S4 的准备和 Compose 阶段明显慢于旧参考，且 `normal_runtime_inputs.json` 达约 1.38 GB；这已记录为下一轮性能工作，不在提交前冒险重写稳定路径。运行时主要时间由主机网络变更和 post-apply/报告开销构成，偶发尖峰需要在后续以操作范围和后台 maintenance 重叠继续归因。

## 重启环境证据

当前语义 S4 在真实硬重启后的 boot ID `02a62489-8564-4a00-ac37-49f18ee77132` 上执行，boot start 为 2026-07-12 23:30 EDT。内核 5.4、Docker 19.03.8、Compose 1.27.4、Python 3.10.20。重启后通过标准 host MPLS 准备恢复 bridge netfilter/MPLS 条件，连续物理时间 S4 完成真实网络主体与清理。

这证明标准准备流程足以恢复当前主机，并不保证内核、Docker、包或配置漂移后的未来主机。昂贵运行前仍执行 `environment_and_running_ZH.md` 中的短检查序列。

## 更新规则

- 廉价测试完成后更新本页，不更新迁移验收文件；
- 真实运行只有最终结果和零残留证据可用后才记录；
- 失败或中断运行仍是证据，不能描述成新基线；
- 环境事实包含观察时的 boot/kernel/dependency 身份；
- 历史迁移记录只有纠正事实错误时才改，并明确写出修正说明。
