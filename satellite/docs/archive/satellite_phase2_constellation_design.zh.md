# 中等规模 Constellation + Continuous Runtime Handover 设计草案

本文档是当前 `satellite/` 主线之上的下一阶段设计草案。

目标不是推翻现有 runtime/controller 结构，而是在保持现有边界不变的前提下，把当前
`6 ST / 3 GW / 3 UT / t0,t1` 的原型，扩成一个更真实、可持续演示的中等规模
`LEO constellation` prototype。

## 设计前提

以下边界保持不变：

- 不修改 `SEED core`
- 不恢复 `anchor/public inbound/UT-direct/hairpin/t2`
- 不改变当前 `CGNAT-like outbound` 语义
- 不引入 host-side parallel mutation
- 不做复杂 distributed synchronization
- 不实现完整 in-emulation control-plane delivery
- 保持 current local-agent direction
- `apply_scenario` 继续保持串行安全
- `prepare-ut-egress-policy` 继续是 static guardrail
- `install-ut-egress-mpls` 继续只下发 dynamic delta
- verify 继续逐条执行
- 当前阶段保持 `logical topology abstraction`
- 不过早进入真实轨道物理 / 波束 / 连续位置建模
- 当前对 orbital realism 的要求以“同轨稳定、跨轨动态、`logical_direction` 可表达”即可
- continuous handover demo 的重点是持续 runtime behavior、path transition、forwarding correctness
- 当前不把动画效果或极限性能优化作为优先目标

因此，这一轮的主线不是换执行模型，而是：

- 扩大 static topology inventory
- 让 runtime scenario 更像真实 orbital handover
- 让 continuous demo 从“全量清理 + 两态切换”演进成“局部 topology handover + agent-based dynamic reinstall”

## 当前实现里最相关的现状

当前代码已经具备这一轮设计所需的主骨架：

- [build_topology.py](/home/seed/skyseed/satellite/build_topology.py:1)
  负责 compile-time inventory 和 `topology.json`
- [controller.py](/home/seed/skyseed/satellite/controller.py:109)
  已有显式 `SCENARIO_DEFINITIONS`
- [controller.py](/home/seed/skyseed/satellite/controller.py:4364)
  的 `apply_scenario()` 已经是“按 target scenario 收敛 slot state”
- [mpls_control.py](/home/seed/skyseed/satellite/mpls_control.py:957)
  已能从当前 attached graph 自动计算 `UT -> selected GW` 最短 path
- [controller.py](/home/seed/skyseed/satellite/controller.py:4063)
  已把 `prepare/install/cleanup` 三类 dynamic runtime update 送进 node-local transient agent
- [mpls_scenario_loop.py](/home/seed/skyseed/satellite/mpls_scenario_loop.py:1)
  已经有 continuous serial loop 的雏形

但当前仍有几个明显的“两轨道/小规模”硬编码：

- [build_topology.py](/home/seed/skyseed/satellite/build_topology.py:42)
  到 [build_topology.py](/home/seed/skyseed/satellite/build_topology.py:63)
  的卫星/轨道定义仍是固定 `A/B`
- [build_topology.py](/home/seed/skyseed/satellite/build_topology.py:616)
  到 [build_topology.py](/home/seed/skyseed/satellite/build_topology.py:649)
  的 cross-plane slot inventory 只有 `ab-cross-slot*`
- [controller.py](/home/seed/skyseed/satellite/controller.py:2090)
  左右的 `_parse_cross_request()` 仍隐含“其中一个轨道必须是 orbit-a”
- [controller.py](/home/seed/skyseed/satellite/controller.py:2336)
  到 [controller.py](/home/seed/skyseed/satellite/controller.py:2350)
  的 `connect_cross_plane()` 目前把“每颗星最多一个 cross-plane slot”当成全局约束
- [controller.py](/home/seed/skyseed/satellite/controller.py:109)
  的 scenario schema 仍以单 `verify_ut_id` 和单 expected path 为主
- [mpls_scenario_loop.py](/home/seed/skyseed/satellite/mpls_scenario_loop.py:551)
  之后的 loop 仍然以“每轮 cleanup forwarding -> apply -> install -> verify -> cleanup”作为主流程

这些点都不需要推翻，只需要做泛化和分层。

## 建议的目标规模

第一批实现建议先落到：

- `12` satellites
- `4` gateways
- `6` user terminals

原因：

- `12 ST` 足够形成 `3 planes x 4 sats` 的更真实 topology
- `4 GW` 足够让 selected gateway 切换在 egress path 上明显可见
- `6 UT` 已明显大于当前 `3 UT`，但仍可 debug
- 相比直接上 `8 UT`，`6 UT` 更容易先把 policy prepare fan-out、all-UT install/cleanup、loop 观察稳定下来

如果第一批稳定，再把 `UT` 扩到 `8` 会比较自然。

## 建议的 topology 草案

### 1. 轨道平面

建议从两轨扩成三轨，每轨四星：

```text
Plane A: sat-a1 sat-a2 sat-a3 sat-a4
Plane B: sat-b1 sat-b2 sat-b3 sat-b4
Plane C: sat-c1 sat-c2 sat-c3 sat-c4
```

这里的“多平面”仍然是 logical topology abstraction，不表示要在这一阶段引入：

- 连续轨道位置
- 星下点移动
- 波束 footprint
- 时间连续的可见性窗口
- 几何驱动的链路预算

当前只需要：

- plane membership
- same-plane stable neighbors
- cross-plane dynamic neighbor set
- `logical_direction` 驱动的 runtime attach/connect 约束

### 2. 同轨链路

同轨 `front/back` 保持 compile-time fixed link，但从当前“链”改成“环”：

```text
A: a1-a2, a2-a3, a3-a4, a4-a1
B: b1-b2, b2-b3, b3-b4, b4-b1
C: c1-c2, c2-c3, c3-c4, c4-c1
```

这样做的原因：

- 更接近“同轨邻居基本稳定”的目标
- 每颗星都保持两个固定同轨邻居
- shortest path 会出现更真实的绕行/反向绕行选择
- 不需要改现有 `mpls_control.py` 的 BFS path model

### 3. 跨轨链路

初版建议只做相邻轨道之间的 cross-plane：

- `A <-> B`
- `B <-> C`

不做 `A <-> C` 直接 cross-plane。

这样可以：

- 保持 topology 真实度
- 避免 runtime slot 数量膨胀过快
- 让 `A -> C` 路径自然穿过 `B`，更容易观察 path transition

每个相邻轨道对建议先给 `4` 个 runtime slots：

- `ab-cross-slot0..3`
- `bc-cross-slot0..3`

总数 `8` 个 cross-plane slots，仍在可 debug 范围内。

### 4. cross-plane 方向语义

建议把 `left/right` 真正用起来，而不是只把它当字符串：

- `A -> B` 使用 `A:right` / `B:left`
- `B -> C` 使用 `B:right` / `C:left`

这有一个很关键的现实收益：

- `B` 面的卫星可以同时占用一个“朝 A 的 left cross-link”和一个“朝 C 的 right cross-link”

这要求把当前 controller 的 cross-plane 冲突规则，从：

- “一个卫星不能出现在任何其他 occupied cross-plane slot”

调整成：

- “同一颗卫星在同一 logical direction 上只能占一个 occupied cross-plane slot”

也就是冲突粒度从 `satellite_id` 变成 `(satellite_id, logical_direction)`。

这一步非常关键，因为如果不改，三轨结构里 `B` 面卫星永远不能同时连向 `A` 和 `C`。

### 5. Gateway 分布

建议扩成 4 个 gateway，并显式分散到不同 `IX`：

- `gw1 -> ix100`
- `gw2 -> ix101`
- `gw3 -> ix103`
- `gw4 -> ix104`

对应上游 peering 可以用：

- `gw1 @ ix100 -> AS2`
- `gw2 @ ix101 -> AS12`
- `gw3 @ ix103 -> AS3`
- `gw4 @ ix104 -> AS4`

这样比当前 `gw1/gw2/gw3` 更适合做 visible gateway switch：

- uplink 接口不同
- NAT realization 不同
- miniInternet egress 区域更分散

### 6. UT 分布

第一批建议 `6 UT`，并且每个 UT 的 candidate serving satellites 明确收窄到局部区域，而不是像当前一样默认所有 satellite 都是 candidate。

原因很实际：

- 当前 `prepare-ut-egress-policy` 会在 candidate serving satellites 上安装 static guardrail
- 如果 `12 ST` 时代每个 `UT` 的 candidate list 还是全星座，prepare fan-out 会明显膨胀
- 实际上 demo 里每个 `UT` 只会在少量邻近卫星间 handover

建议做成类似：

- `ut1`: `sat-a1, sat-a2, sat-b1`
- `ut2`: `sat-a2, sat-a3, sat-b2`
- `ut3`: `sat-b1, sat-b2, sat-c1`
- `ut4`: `sat-b2, sat-b3, sat-c2`
- `ut5`: `sat-c2, sat-c3, sat-c4`
- `ut6`: `sat-a3, sat-a4, sat-b4`

这里的重点不是这些具体组合必须完全照搬，而是：

- 每个 `UT` 的 direct attach 候选星要小而真实
- scenario 里的 handover 主要在局部邻近卫星之间发生
- `prepare-ut-egress-policy` 的静态安装范围必须被控制住

## Runtime dynamics 设计

### 1. 不是“全网翻转”，而是“滚动局部 handover”

建议把 scenario 组织成一个 `4-state` cycle，例如：

- `r0`: baseline
- `r1`: east corridor handover
- `r2`: center corridor handover
- `r3`: west corridor handover

相邻状态只改变一个局部区域。

例如：

- `r0 -> r1`
  只改 `ut1/ut2`、`gw1`，以及一小组 `ab-cross-slot`
- `r1 -> r2`
  只改 `ut3/ut4`、`gw3`，以及一小组 `bc-cross-slot`
- `r2 -> r3`
  只改 `ut5/ut6`、`gw2` 或 `gw4`，以及另一小组跨轨 slot
- `r3 -> r0`
  只做 wrap-around 的局部恢复/前移

这样有几个好处：

- 满足“局部 runtime handover”的真实感
- `apply_scenario` 仍然可以保持“目标态收敛”的简单模型
- 相邻 scenario 之间的 diff 会更小，更适合 continuous demo
- 更符合当前阶段“持续 runtime behavior + path transition + forwarding correctness”的重点

### 2. scenario 仍保持“全量 target snapshot”

建议不要把 `apply_scenario` 改成 patch API。

现有模型已经很好：

- scenario 是一个完整 target occupancy snapshot
- `apply_scenario()` 自己根据当前状态和目标状态算 release/attach/connect

因此更适合的做法是：

- 每个 `r0/r1/r2/r3` 继续定义完整 `managed_slots`
- 但在设计上保证“相邻 scenario 的 slot diff 很小”

这样既保持当前安全边界，也不会引入新的 transaction 复杂度。

### 3. scenario schema 建议扩展，但保持兼容

当前 scenario 只围绕：

- `verify_ut_id`
- `expected_serving_satellite_id`
- `expected_selected_gateway_id`
- `expected_satellite_path`

中规模 demo 建议逐步扩成：

- 保留 `verify_ut_id` 作为兼容字段
- 新增 `observe_ut_ids`
- 新增 `expected_ut_paths`
- 新增 `transition_region`
- 新增 `notes`

大致形状可以是：

```json
{
  "verify_ut_id": "ut1",
  "observe_ut_ids": ["ut1", "ut3", "ut5"],
  "transition_region": "east",
  "expected_ut_paths": {
    "ut1": {
      "serving_satellite_id": "sat-a2",
      "selected_gateway_id": "gw1",
      "satellite_path": ["sat-a2", "sat-b2"]
    },
    "ut3": {
      "serving_satellite_id": "sat-b2",
      "selected_gateway_id": "gw3",
      "satellite_path": ["sat-b2", "sat-c2"]
    }
  }
}
```

第一批实现里，`verify_scenario()` 甚至可以先继续只严格验证一个 `verify_ut_id`，然后把其余 `observe_ut_ids` 先用于 loop summary 和 demo 输出。

## Continuous runtime handover 策略

### 1. 需要和当前 smoke loop 分层

当前 [mpls_scenario_loop.py](/home/seed/skyseed/satellite/mpls_scenario_loop.py:1)
更像 regression/stability loop：

- 每轮 cleanup forwarding
- apply scenario
- install
- verify
- cleanup forwarding

这对 correctness 很好，但对“持续 handover demo”不够自然。

建议保留当前 loop 作为：

- strict smoke / residue detector

再增加一个新的 continuous demo runner，或者把现有 loop 扩成两个 mode：

- `strict-smoke mode`
- `continuous-handover mode`

这个 continuous mode 的目标应明确收敛到四件事：

- 小 diff scenario
- corridor rolling dynamics
- agent-based runtime update 稳定
- dataplane 连续可达且无 stale forwarding residue

### 2. continuous mode 的建议主流程

建议 continuous mode 采用下面的节奏：

1. 一次性 `prepare-all-ut-egress-policies`
2. `apply_scenario(r0)`
3. `install-all-attached-ut-egress-mpls`
4. 启动多 `UT` 长时间 ping 外部 host
5. 每 `5~10s` 执行一次局部 scenario 切换

每次切换建议做：

1. 识别本轮受影响 `UT`
2. 仅 cleanup 这些 `UT` 的 dynamic MPLS state
3. `apply_scenario(next_state)`
4. 仅 install 这些 `UT` 的新的 dynamic MPLS state
5. verify 本轮受影响 `UT`
6. 每隔 `N` 轮再做一次 all-UT verify / residue observation

这个流程有两个关键点：

- `static_guardrail` 只在 demo 启动前做一次
- forwarding 的 cleanup/install 尽量收敛到“本轮受影响的 `UT`”

这比“每轮 cleanup all / install all”更符合本阶段目标。

这里的“连续可达”不应理解成：

- 零丢包 handover
- make-before-break
- session continuity guarantee

当前阶段更现实的目标是：

- 小范围 topology mutation 期间允许短暂抖动
- 新路径应快速恢复
- 未受影响 corridor 的 `UT` 尽量不被牵连
- 每轮之后 forwarding residue 必须为空或与当前 attached path 严格一致

### 3. 受影响 UT 的判定方式

建议不要先做复杂 distributed sync，而是让 controller 本地做 plan diff。

可行的第一版策略：

- 切换前读取当前 scenario 下的 attached `UT` plans
- 应用下一 scenario 后，再读取新的 attached `UT` plans
- 对比每个 `UT` 的：
  - serving satellite
  - selected gateway
  - satellite path
  - labels
- 只把发生变化的 `UT` 标记为 impacted

这样可以做到：

- 拓扑 mutation 仍串行
- forwarding mutation 仍串行
- 但 runtime handover 的影响面是局部的

### 4. v1 和 v2 的实现分层

为了控制风险，建议把 continuous handover 分两步做：

`v1`

- topology transition 是局部的
- forwarding 仍允许先用 `cleanup-all-attached -> apply -> install-all-attached`
- 目标是先把中规模 topology、scenario matrix、loop 观察稳定起来

`v2`

- topology transition 仍局部
- forwarding 改成 impacted-UT only cleanup/install
- 目标是让未受影响 `UT` 的持续 ping 更连续

这两步都不需要 host-side parallelism，也不需要复杂同步协议。

## visualization / observable path transition 预期效果

当前 `SEED map` 仍然更适合作 compile-time topology view，而不是 runtime dynamic renderer。

所以这一阶段的“可观察性”建议分三层：

### 1. controller/loop summary

每轮打印：

- 当前 scenario
- 本轮 transition region
- 受影响 `UT`
- 每个观察 `UT` 的 serving satellite
- selected gateway
- `forward_mpls_nodes`
- labels

这一步最稳，也最贴合当前实现。

### 2. multi-UT continuous ping watcher

建议至少让 `3` 个 `UT` 持续 ping 外部 host：

- `ut1`
- `ut3`
- `ut5`

这样每次局部 handover 都能看到：

- 只有对应区域的 `UT` 出现短暂丢包/恢复
- 不相关区域的 `UT` 尽量保持稳定

### 3. transition-focused dataplane evidence

不要一开始就追求“全网实时动画”。

更现实的第一批观测应该是：

- loop summary 里的 path change
- 持续 ping 的 loss/recovery pattern
- 对 1 个 showcase `UT` 做 targeted `tcpdump` 或 route/LFIB re-read

也就是说，本阶段的“可观察性”优先级应是：

1. path transition 是否清楚
2. dataplane correctness 是否稳定
3. residue 是否持续为零
4. agent-based update 是否可解释
5. 视觉效果

后续如果要更强的视觉效果，再考虑做一个轻量自定义 renderer，直接读：

- `topology.json`
- `link_state.json`
- 当前 controller-resolved `UT` path summaries

而不是依赖 compile-time `SEED map` 自动反映 runtime slot connect/disconnect。

## 建议先实现的部分

第一批建议直接实现下面这些：

### 批次 1：topology 数据模型泛化

- 把 `build_topology.py` 里的两轨/六星硬编码抽成 plane definitions
- 扩成 `3 planes x 4 sats`
- 加 `gw4`
- 扩到 `6 UT`
- 把 `topology.json` 的 `orbit_id` / `prototype_scope` / `slot_inventory` 泛化
- cross-plane slot 从单一 `ab-cross-slot*` 扩成 `ab-*` 和 `bc-*`

### 批次 2：controller runtime 泛化

- 去掉 `_parse_cross_request()` 里的 `orbit-a` 假设
- cross-plane 冲突检查从“每星最多一个 cross slot”改成“每星每方向最多一个 cross slot”
- scenario schema 增加 `observe_ut_ids` / `expected_ut_paths`
- `verify_scenario()` 先最小支持多 `UT` summary

### 批次 3：MPLS intent scope 扩容

- `SUPPORTED_UT_EGRESS_INTENTS` 扩到 `ut1..ut6`
- `GATEWAY_NAT_REALIZATIONS` 扩到 `gw4`
- 每个 `UT` 的 `candidate_satellite_ids` 收窄到局部候选集合

### 批次 4：continuous demo runner

- 新增 continuous-handover mode
- 周期 `5~10s`
- 默认多 `UT` 观察
- 输出 per-UT path transition summary
- 先做 `v1`，再演进到 impacted-UT only `v2`

这一批里实现优先级建议明确为：

1. scenario diff 小而稳定
2. corridor rolling sequence 清晰
3. update/verify/cleanup residue 可靠
4. 多 `UT` 持续 ping 观测
5. 更漂亮的输出格式

## 建议后续再扩展的部分

下面这些更适合放到第二批之后：

- `UT` 从 `6` 扩到 `8`
- `A <-> C` 直连 cross-plane
- impacted-UT only cleanup/install 的更细粒度优化
- persistent local agent
- background topology watcher
- runtime path custom renderer
- versioned forwarding state / make-before-break
- active session continuity
- 更多 external test targets 和 multi-flow 观察

## 结论

这一轮最合适的方向不是改执行边界，而是把现有主线往前推一层：

- topology 从 `2 planes / 6 sats` 扩成 `3 planes / 12 sats`
- scenario 从 `t0/t1` 两态扩成局部滚动 `r0/r1/r2/r3`
- handover 从“全量 smoke 切换”演进成“局部 topology mutation + agent-based dynamic forwarding reinstall”
- observability 先以 path summary + continuous ping + targeted dataplane evidence 为主

如果按实现风险排序，最值得先动的三个点是：

1. `build_topology.py` 的多平面 inventory 泛化
2. `controller.py` 的 generic cross-plane semantics
3. continuous demo runner 的 `v1` 结构

等这三步稳定之后，再把 impacted-UT only runtime update 做进去，会最顺。
