# Satellite Phase 2 Slot-Based Dynamic Link Design 中文说明

英文版：
[satellite_phase1_design.md](/home/seed/seed-emulator/examples/satellite/docs/satellite_phase1_design.md:1)

本文档描述当前主线的 satellite phase2 slot-based dynamic link prototype。
旧的 candidate-link / snapshot activation 主线在这个阶段不再继续保留为主叙事。

## 范围

- 主要工作范围保持在 `examples/satellite/`
- 默认不改 SEED core
- 规模固定为 3 `UT`、6 `ST`、3 `GW`
- 恢复完整的 `B00 miniInternet` 作为地面互联网结构
- 当前只验证 slot-based runtime link model
- 验证当前“已 attach 且受支持 `UT`”的最小 controller-managed
  `UT egress MPLS`，以及 selected-`GW` `NAT/general return path`
- 不要求真实 `MPLS`、大规模扩展或自定义前端

当前阶段明确不做以下内容：

- 不恢复旧的 `forwarding_state`、`candidate`、`snapshot` 主流程
- 不引入真实分布式 `MPLS` 控制面协议，例如 `LDP`、`RSVP`、`SR`
- 不让 satellite runtime side 跑 `OSPF/iBGP/eBGP`
- 不保证 scenario/gateway 切换时的 active session continuity
- 不支持并发 runtime transaction 或 multi-writer safety

## 运行时边界

- SEED 负责创建基础容器、fixed intra-plane links，以及 compile-time `miniInternet`
- `controller.py` 负责 runtime slot bridge 创建与 connect/disconnect
- 所有 runtime state 都显式记录在 `link_state.json`
- 不依赖隐藏的后台状态机
- `link_state.json` 仍然只记录 satellite slot runtime 和 `fixed_isl`
- `miniInternet` 内部链路不会写进 `link_state.json`

## UT 地址语义

当前原型明确区分两类地址：

- `UT stable identity address/prefix`
  - `ut1 = 100.64.0.1/32`
  - `ut2 = 100.64.0.2/32`
  - `ut3 = 100.64.0.3/32`
  - 这些地址挂在 `dummy0` 上，在 `UT-ST` attach/detach 过程中保持不变
  - 后续 forwarding/MPLS 阶段应把它们视为业务 endpoint / `FEC` / 用户身份
- `UT-ST runtime slot link address`
  - 例如 `10.90.x.x/29`
  - 这些地址只表示当前 access link 上的链路下一跳
  - 不要把 slot link IP 当作用户身份地址

## Routing 边界

- 恢复后的 `miniInternet` 内部继续使用 SEED 的 `Routing + Ebgp + Ibgp + Ospf`
- `GW` 的地面 `IX` 上联继续使用 SEED 的 `eBGP`
- `AS250` 和 `AS65000` 的卫星 runtime side 明确从 SEED `Ospf/Ibgp` 中屏蔽
- `UT-ST`、`GW-ST`、cross-plane `ST-ST`，以及当前固定轨内 `ISL`，仍然由 controller 和 `link_state.json` 管理

## 链路模型

### `UT-ST`

- 每个 `UT` 预留 2 个 access slots
- 每个 slot 对应 1 个 Docker bridge network
- 初始化阶段只连接 `UT` 端
- 运行时动态连接或断开 1 个 `ST`
- 如果没有空闲 slot，controller 必须清晰失败

### `GW-ST`

- 每个 `GW` 预留 2 个 gateway slots
- 每个 slot 对应 1 个 Docker bridge network
- 初始化阶段只连接 `GW` 端
- 运行时动态连接或断开 1 个 `ST`
- 如果没有空闲 slot，controller 必须清晰失败

### 固定轨内 `ISL`

- `sat-a1 <-> sat-a2 <-> sat-a3`
- `sat-b1 <-> sat-b2 <-> sat-b3`
- 这些链路保持 compile-time fixed p2p links
- 它们会以 `fixed_isl` 形式出现在 `link_state.json`

### 跨轨 `ISL`

- orbit-a 和 orbit-b 共享 3 个可复用 cross-plane slots
- 每个 slot 对应 1 个共享 Docker bridge network
- 初始化阶段只创建 bridge，不连接任何 `ST`
- 运行时把两颗 `ST` 同时连接到同一个 slot bridge
- 断开时移除两端，slot 回到空闲状态

## 状态模型

`link_state.json` 是 controller 拥有的 runtime state 文件。

每个 slot entry 记录：

- `slot_name`
- `slot_type`
- `network_name`
- `docker_network_name`
- `subnet`
- `bridge_gateway_ip`
- `preconnected_endpoints`
- `dynamic_endpoints`
- `occupied`
- `binding`

对于 cross-plane slot，`binding` 记录：

- `satellite_a_id`
- `satellite_b_id`
- `satellite_a_direction`
- `satellite_b_direction`

对于固定轨内链路，`slot_type=fixed_isl` 且 `occupied=true`。

## 安全原则

- 每次 `docker network connect` 后，目标容器必须恰好新增 1 个 interface
- 如果新增数量不是 1，立即 abort
- 只允许 rename 刚刚确认出的新 interface
- 不 rename 任何不确定的已有 interface
- 失败时不写 `link_state.json`
- cross-plane 第二端连接失败时，必须回滚第一端

## 当前 CLI

初始化所有 runtime slot bridges 和固定端预连接：

```bash
python3 examples/satellite/controller.py --init-slot-runtime
```

查看 runtime state：

```bash
python3 examples/satellite/controller.py --show-link-state
```

运行只读的 `MPLS-0` capability probe：

```bash
python3 examples/satellite/controller.py --probe-mpls
python3 examples/satellite/controller.py --probe-mpls --probe-report examples/satellite/configs/mpls_probe.json
```

这个 probe 会检查：

- 宿主机侧 `mpls_router` 和 `mpls_iptunnel` 的 `modprobe` 可用性
- 宿主机侧 `net.mpls.platform_labels` 是否可见且为非零值
- 宿主机侧 `iproute2` 是否支持 `ip -f mpls route` 和 `encap mpls`
- 当前正在运行的 `ST/GW` 容器里，是否能看到且启用非零值的 `MPLS` 相关 sysctl，以及 Docker capability posture 是否满足

运行当前 controller-driven 的 `MPLS-2` static `LSP` workflow：

```bash
python3 examples/satellite/controller.py --install-static-mpls-lsp sat-a1:sat-b1:gw1
python3 examples/satellite/controller.py --verify-static-mpls-lsp sat-a1:sat-b1:gw1
python3 examples/satellite/controller.py --cleanup-static-mpls-lsp sat-a1:sat-b1:gw1
```

现有的 regression wrapper 仍然保留：

```bash
python3 examples/satellite/mpls_static_lab.py
python3 examples/satellite/mpls_static_lab.py --cleanup
```

连接和释放 `UT`：

```bash
python3 examples/satellite/controller.py --attach-ut ut1:sat-a1
python3 examples/satellite/controller.py --attach-ut ut1:sat-a1 --slot-name ut1-access-slot1
python3 examples/satellite/controller.py --detach-ut ut1
```

连接和释放 `GW`：

```bash
python3 examples/satellite/controller.py --attach-gw gw1:sat-b1
python3 examples/satellite/controller.py --attach-gw gw1:sat-b1 --slot-name gw1-gateway-slot1
python3 examples/satellite/controller.py --detach-gw gw1
```

连接和释放 cross-plane `ISL`：

```bash
python3 examples/satellite/controller.py --connect-cross sat-a1:sat-b1:left:right
python3 examples/satellite/controller.py --connect-cross sat-a1:sat-b1:left:right --slot-name ab-cross-slot2
python3 examples/satellite/controller.py --release-slot ab-cross-slot0
```

如果 attach/connect 时不指定 `--slot-name`，controller 会按 slot 名字排序后选择第一个空闲 slot。

## Bootstrap 接口注意事项

- 某些容器里可能还保留着 SEED bootstrap 阶段留下的 `eth*` 接口，并且状态是 `DOWN`
- 这本身不被视为 slot runtime bug
- controller 的 slot 安全校验方式，是比较 `docker network connect` 前后的 interface 集合差异
- 成功条件是“恰好新增 1 个新接口”，而不是“所有旧 bootstrap `eth*` 都必须消失”

## 当前验证目标

当前 prototype 只需要证明以下几点：

- slot 资源是有限且可见的
- runtime bridge 可以复用
- controller-visible state 一致
- 局部失败时可以安全回滚
- 通过 Docker 和 `link_state.json` 容易观察与调试

作为下一阶段的入口，`MPLS-0` 额外增加一个验证问题：

- 当前 host 和 Docker runtime，是否能在不改变 slot-runtime state model 的前提下，支撑后续 centralized Linux kernel `MPLS` dataplane
- 这里的“支撑”现在需要明确到：host 和每个参与 MPLS 的 runtime 容器里，`net.mpls.platform_labels` 都必须大于 `0`，不能只是模块已加载、sysctl 路径可见

`MPLS-2` 额外澄清了一条边界：

- 现在可以由 `controller` 显式触发 static `LSP` intent 和静态 `LFIB/IP` route install
- `controller` 会在安装 Linux `MPLS` 路由前，先依据 `link_state.json` 验证所需 path 当前是否真实存在
- 这仍然不是 topology-driven routing、动态 path computation，也不是 handover-aware `MPLS` orchestration

`MPLS-3` 再往前推进了一步，但仍保持在 example/runtime 层：

- `controller` 现在可以显式处理单 `UT` 的 Internet egress intent，
  也提供了面向当前所有已 attach 且受支持 `UT` 的串行 wrapper
- 它会从 `link_state.json` 解析当前 serving satellite、occupied `gw_access`，并在 active satellite graph 上选择最少跳数 path
- 它会把这条 path 转成显式 `UT egress MPLS intent/profile`，再下发
  更自然的 `default Internet egress` Linux `MPLS/IP` route：
  `UT` 只拿到默认路由指向当前 serving satellite，而 serving satellite
  会把来自该 `UT` 的 non-local traffic 放进专用 source-policy table，
  再结合 controller 从 `topology.json` 推导出的 local/internal
  exclusions 做默认外发分类，以及最小 selected-`GW`
  `NAT/general return path`

当前 `MPLS-3` 的 dataplane 边界已经更清楚：

- `UT stable identity` 当前仍挂在 `dummy0` 上
- 它仍然是 controller/FEC identity，不是当前 Linux 业务 source address
- 当前 `UT egress` 的 Linux dataplane realization 依赖 occupied `UT access slot`、active interface 和 access-link IP
- 当前 `UT egress` 的核心 forwarding 语义已经不再依赖
  enumerated external host-prefix allowlist；controller 现在会从
  `topology.json` 自动推导 local/internal exclusions，再把其余 `UT`
  源流量视为 default Internet egress，送往当前 selected gateway
- 只要 host `bridge netfilter passthrough` 已准备好
  `net.bridge.bridge-nf-call-{iptables,ip6tables,arptables}=0`，
  controller-managed `ut1 -> 10.154.0.129` 就已经可以通过 Linux kernel `MPLS` 跑通端到端
- 这组 host bridge-netfilter 准备动作目前仍属于 example/runtime 逻辑，不应视为 `SEED core` 抽象
- 当前固定的 regression 入口是 `examples/satellite/mpls_ut_egress_smoke.py`，
  它会把路径固定在 `ut1 -> sat-a1 -> sat-b1 -> gw1 -> 10.154.0.129`，
  分层验证 dataplane 证据，再清掉 forwarding state，但不释放 runtime slot
- 当前 return path 是刻意收窄的最小方案：selected gateway 会在自己
  bootstrap-side miniInternet IP（`10.65.21.2`、`10.65.22.2`、
  `10.65.23.2`）上安装 Linux `iptables` `SNAT/FORWARD` 规则，
  回包依赖 conntrack/NAT reverse mapping
- 当前 all-UT scope 仍然是刻意收窄的：默认 `t0/t1` scenario 实际覆盖
  `ut1`、`ut2`、`ut3`；当前已验证的 all-UT 范围仍然只限于这些串行
  example/runtime scenario 切换
- 当前步骤仍然不保证 gateway/scenario 切换过程中的 active session continuity
- controller state mutation 当前仍然只能串行执行，不要把 attach/release/connect
  这类共享 `link_state.json` 的动作并行化

`MPLS-4` 第一阶段保持上述边界不变，但增加了显式 scenario 收敛能力：

- `controller.py --apply-scenario t0|t1` 现在可以一次收敛一个命名 scenario，
  先释放不匹配的 managed slot，再串行补齐所需的 `UT`、`GW` 和 cross-plane binding
- `controller.py --verify-scenario t0|t1` 会验证当前 `link_state.json`
  是否符合目标 scenario，并检查 `ut1` 当前是否解析到期望的
  serving satellite、selected gateway 和 shortest satellite path
- `examples/satellite/mpls_scenario_smoke.py` 会顺序执行
  `cleanup forwarding -> apply scenario -> verify scenario -> install ut1 egress MPLS -> verify dataplane -> cleanup forwarding`，
  用来证明 topology 切换可以在不重启容器的前提下原地完成
- `examples/satellite/mpls_scenario_smoke.py --scenario t0|t1 --all-ut`
  现在会把同样的串行 scenario flow 应用到当前所有已 attach 且受支持的
  `UT`，并额外验证一个任意 miniInternet host 目标（`10.160.0.71`）
- 真实 runtime 容器里的 `default Internet egress` 现在也已经覆盖默认三个
  `UT`（`ut1`、`ut2`、`ut3`），并完成过一次严格的
  `cleanup all -> apply scenario -> reinstall all -> verify all` 闭环验证
- `single-UT cleanup` 现在必须保留仍被其他已安装 `UT egress` plan 共用的
  `MPLS input` 接口；example/runtime controller 现在已经把这一点纳入
  forwarding cleanup correctness
- 当前已经记录了真实的串行 runtime 耗时，这些数字也直接说明了后续为什么需要并行化的 runtime/controller update：
  `apply_scenario t1 = 44.78s`；一次严格的 all-UT
  `t1 -> cleanup all -> t0 -> install all -> verify all` 里，
  `cleanup_all=11.35s`、`apply_scenario t0=48.51s`、
  `install_all=39.98s`、`verify_all=16.17s`
- cleanup 仍然只清 forwarding state，不释放当前选中的 runtime topology
- 这一步是最终持续 `t0 -> t1 -> ...` scenario loop 的前置验证；
  但仍然不解决 active session continuity，也不支持并发 mutation

`MPLS-4` 第二阶段在保持同样 example/runtime 边界的前提下，
加入了第一版显式 loop runner：

- `examples/satellite/mpls_scenario_loop.py` 会在不重启容器的前提下，
  持续重复 `t0 -> t1 -> t0 -> t1 -> ...`
- 现在直接运行 `python3 examples/satellite/mpls_scenario_loop.py`
  就会启动这个 demo loop：默认无限运行、每轮间隔 5 秒，
  也可以用 `--max-rounds` 做 bounded soak run
- 每一轮仍然严格执行
  `cleanup forwarding -> apply scenario -> verify scenario -> install ut1 egress MPLS -> verify dataplane -> cleanup forwarding`
- loop runner 会在每轮成功后打印当前 scenario、selected gateway、
  selected path，以及 forward/reverse labels
- 用户按 `Ctrl-C` 时，loop runner 会做 best-effort forwarding cleanup，
  并在退出前打印最终 `link_state`
- 每轮成功后还会做第一版 residue/stability 观察：
  检查 forwarding cleanup 是否为空、scenario/link_state 是否一致、
  managed slot bridge occupancy 是否匹配，以及 satellite runtime interface
  是否出现额外 stale residue
- 如果任一步串行 runtime mutation 或 MPLS install/verify 失败，
  loop runner 会终止当前轮，做 best-effort forwarding cleanup，
  打印当前 `link_state`，然后退出，而不是继续带着混合不确定状态运行
- 当前已经拿到第一批 bounded soak 结果：
  10 轮 run 已完整成功；50 轮目标在人工停止前已经连续成功 44 轮，
  在这些已完成轮次里没有观察到 stale `LFIB`、stale IP route、
  `link_state` mismatch，或 satellite runtime interface residue
- `mpls_scenario_loop.py --all-ut` 现在已经提供出来，
  但默认 continuous loop 和当前最长的 repeated-transition soak 证据
  仍然集中在默认 ut1-only path；验证 all-UT 改动时建议优先使用
  all-UT smoke wrapper
- 如果 `Ctrl-C` 恰好打在 in-flight runtime mutation 中间，
  当前 forwarding cleanup 仍然只是 best-effort，
  topology 可能停在 partial transition 状态；继续之前应显式重跑目标
  scenario 或 smoke wrapper，把 topology 收敛回确定状态
- 这已经成为当前 repeated transition validation 的主要 regression/stability 入口，
  但仍然没有后台 topology watcher，或通用长期 scenario API

## Serialized Runtime Boundary

当前 runtime controller 必须明确视为：

`single-writer / serialized-operation safe only`

也就是说：

- 当前 demo 和 smoke flow 都假设 runtime mutation 严格串行执行
- 当前不支持并行 attach/release/connect/MPLS update
- 当前不建议同时开多个 controller 终端或进程并发修改 runtime state

当前这条边界对串行 demo 已经足够，但未来一旦并行或规模扩大，
下面这些风险会直接暴露出来：

1. interface diff 识别风险
   当前 `docker network connect` 后通过 before/after interface diff 识别新接口。串行时通常只新增一个接口；并行时可能同时新增多个接口，导致 rename、IP config、`link_state` 写错。当前原则应继续保持：`new_ifaces` 必须 exactly one，否则 abort/rollback。

2. `link_state.json` 写入竞态
   当前状态更新仍然是 read-modify-write JSON。并行 controller 操作会互相覆盖。未来要扩展时，需要 file lock、transaction、single-controller event loop，或 versioned state。

3. rollback 互相踩踏
   并行执行时，一个 operation 的 rollback 可能删除另一个 operation 刚创建的资源。未来需要 transaction id、per-slot lock，或 per-node lock。

4. slot ownership 过于粗糙
   当前 slot 主要还是 `occupied=true/false`。未来并行 orchestration 需要更细的状态，例如 `free`、`reserved`、`connecting`、`occupied`、`releasing`、`failed`。

5. topology state 和 forwarding state 可能分叉
   并行或部分失败时，可能出现 topology 已切换、但 Linux `MPLS` forwarding 仍是旧路径；或者只有部分节点装上了新 `LFIB`。未来需要 two-phase update、make-before-break，或 versioned forwarding state。

6. label 分配冲突
   当前单 `UT` / 单 `LSP` demo 里 label 还比较可控。未来多 `LSP` 并行时，同一节点上的 local label 不能冲突，因此需要 per-node label allocator。

7. verify / `tcpdump` 互相干扰
   多流并行时，`tcpdump` 可能抓到别的 `LSP` 的包。未来需要 unique flow id、label、payload marker，或更严格的 capture filter。

8. host sysctl 是全局 prerequisite
   `bridge-nf-call-*` 和 `net.mpls.platform_labels` 这类 host/runtime prerequisite，不应被多任务随意来回切换。它们应该作为 runtime prerequisite 被准备和检查，而不是每轮都被当成可并发修改的状态。

9. UT 双连接语义尚未解决
   当前规则仍然是 exactly one active `UT` access attachment：`0` 个是 `ut_not_attached`，`2+` 个是 `ambiguous_ut_attachment`。未来如果要支持 make-before-break，需要显式 `active/standby` 或 session/handover state machine。

10. controller 多实例风险
   未来 runtime 设计应考虑 lock file 或 single-controller daemon，避免多个 controller 进程同时 mutation。

对当前 prototype 来说，操作原则很简单：

- runtime mutation 必须严格串行
- loop runner 和 smoke wrapper 都应视为 single-writer 工具
- 如果中断恰好打在 runtime mutation 中间，继续工作前应重新显式执行目标 scenario 或 smoke wrapper，把 topology 收敛回确定状态

## Runtime Demo 循环

当前 demo 循环刻意保持小而串行：

- `t0`
  - `ut1 -> sat-a1`
  - `gw1 -> sat-b1`
  - cross-plane `sat-a1 <-> sat-b1`
- `t1`
  - release cross-plane
  - detach `ut1`
  - detach `gw1`
  - `ut1 -> sat-a2`
  - `gw1 -> sat-b2`
  - cross-plane `sat-a2 <-> sat-b2`

对应的 demo 驱动脚本是：

`examples/satellite/demo_runtime_handover.py`

它只做串行 runtime 操作，并打印：

- 当前 active links
- 当前 occupied slots
- 当前 runtime 操作
- `link_state.json` 摘要
- 当前状态下的简单连通性探测结果

这个 demo 刻意复用同一组 slot：

- `ut1-access-slot0`
- `gw1-gateway-slot0`
- `ab-cross-slot0`

因此 probe IP 是 slot peer IP，而不是某颗卫星独占的 access/feeder/cross-plane IP。
handover 过程中主要变化的是 `binding`、容器 interface 和瞬时连通性，而不是 probe IP 本身。

注意：这个当前 demo 只验证 slot runtime 的动态链路行为和状态一致性，
不覆盖后面新增的 `UT egress MPLS` / selected-`GW`
`NAT/general return path` workflow，也不恢复旧 forwarding baseline。

## Map 观察边界

- 当前示例的 `SEED map` 地址是 `http://127.0.0.1:8080/pro/map`
- 在这个阶段，应把它视为编译期拓扑可视化
- 当前这个编译期视图应当能看到恢复后的完整 `B00 miniInternet`、`AS250` satellite side，以及 `AS65000` gateways
- 不要把它当作 runtime slot bridge connect/disconnect 的实时渲染结果
- 运行时真相仍然以 controller 输出、容器接口状态和 `link_state.json` 为准
- 由于当前阶段故意不做 forwarding/MPLS，所以还没有稳定的 `UT -> ground host` 端到端探测目标
- 建议的观察方式是：
  - 从 `UT` 持续 ping `10.90.11.1`，并同时观察 `ut1-access-slot0` 的 `binding.satellite_id`
  - 从 `GW` 持续 ping `10.91.11.1`，并同时观察 `gw1-gateway-slot0` 的 `binding.satellite_id`
  - 从 `sat-a1` 或 `sat-a2` 观察 cross-plane peer `10.92.1.2` 的短暂 down/up，并同时观察 `ab-cross-slot0` 的绑定变化
- 当前 demo 的重点是：
  - runtime slot stability
  - dynamic link visualization
  - rollback safety
  - slot reuse
  - Docker network/interface cleanup

## Scenario Matrix

当前经过验证的 runtime matrix 有意只保留两个显式 scenario：

`t0`:

- `ut1 -> sat-a1`
- `ut2 -> sat-a2`
- `ut3 -> sat-b3`
- `gw1 -> sat-b1`
- `gw2 -> sat-a3`
- `ab-cross-slot0: sat-a1(left) <-> sat-b1(right)`
- `ab-cross-slot1: sat-a2(left) <-> sat-b2(right)`
- 预期 outbound selected gateway：
  `ut1 -> gw1`，`ut2 -> gw2`，`ut3 -> gw1`
- 预期 `UT -> external` satellite path：
  `ut1: sat-a1 -> sat-b1`，
  `ut2: sat-a2 -> sat-a3`，
  `ut3: sat-b3 -> sat-b2 -> sat-b1`

`t1`:

- `ut1 -> sat-a2`
- `ut2 -> sat-a3`
- `ut3 -> sat-b1`
- `gw2 -> sat-b2`
- `ab-cross-slot0: sat-a2(left) <-> sat-b2(right)`
- `ab-cross-slot1: sat-a3(left) <-> sat-b3(right)`
- 预期 outbound selected gateway：
  `ut1 -> gw2`，`ut2 -> gw2`，`ut3 -> gw2`
- 预期 `UT -> external` satellite path：
  `ut1: sat-a2 -> sat-b2`，
  `ut2: sat-a3 -> sat-a2 -> sat-b2`，
  `ut3: sat-b1 -> sat-b2`

这个 matrix 现在只用于覆盖：

- 经 `gw1` 的 `UT -> external`
- 经 `gw2` 的 `UT -> external`
- `t0 <-> t1` 之间 selected-gateway 变化
- `t0 <-> t1` 之间 satellite path 变化
- 一次只收敛一个显式 scenario 的串行 runtime reconciliation

## Mainline Model

当前主线模型是类似 Starlink residential 的 outbound Internet access，
而不是 public inbound reachability。

- `UT` 流量通过 `serving satellite -> selected GW -> miniInternet` 出去
- 当前 selected `GW` 承担最小 `SNAT/FORWARD` realization
- `external host -> UT` 不属于当前 baseline
- `UT -> UT` direct reachability 不属于当前 baseline
- `UT stable identity` 仍然有价值，但它现在只是 controller 可见的
  identity/FEC state，不是当前模型里的 public service address

如果未来重新评估 `UT <-> UT`，应把它视为独立的
operator-internal subscriber-routing/core 设计问题，不应再作为当前
baseline 下的 public inbound、`anchor` ingress，或 per-pair shortcut 状态
重新引入。

## Current Runtime Correctness Baseline

因此，当前 correctness baseline 是：

- 应用 `t0` 或 `t1`
- 安装 outbound/current-gateway `UT egress MPLS`
- 验证 `UT -> miniInternet host`
- 做 `t0 -> t1` 切换，重新安装 forwarding，并验证 outbound reachability
  能在新的 selected gateway/path 上恢复

推荐人工验收命令：

```bash
python3 examples/satellite/controller.py --prepare-ut-egress-policy ut1 --timing
python3 examples/satellite/controller.py --apply-scenario t0
python3 examples/satellite/controller.py --verify-scenario t0
python3 examples/satellite/controller.py --install-ut-egress-mpls ut1 --timing
python3 examples/satellite/controller.py --verify-ut-egress-mpls ut1 --timing

python3 examples/satellite/controller.py --cleanup-ut-egress-mpls ut1 --timing
python3 examples/satellite/controller.py --apply-scenario t1
python3 examples/satellite/controller.py --verify-scenario t1
python3 examples/satellite/controller.py --install-ut-egress-mpls ut1 --timing
python3 examples/satellite/controller.py --verify-ut-egress-mpls ut1 --timing
```

当前 `UT egress` plan 已经拆成显式 category：

- `static_guardrail`: source-rule skeleton、汇总后的
  internal/control/runtime guardrail prefixes，以及 legacy 细粒度 exclusion
  清理；通过 `--prepare-ut-egress-policy` 或
  `--prepare-all-ut-egress-policies` 一次性/低频执行
- `dynamic_path`: `UT default route` 与 serving-satellite policy-table
  `default egress route`
- `dynamic_lfib`: 当前 selected gateway/path 相关的 `MPLS input` 和 `LFIB`
- `dynamic_gateway_nat`: 当前 selected gateway 上的 `FORWARD`/`SNAT`/reverse route
- `verify`: route/rule/`LFIB`/`iptables` show 与 dataplane probe
- `cleanup`: 仅清理动态项，保留 static guardrail framework

当前 example/runtime scope 不验证：

- `UT -> UT`
- `external -> UT`
- public inbound addressing
- 基于 `anchor` 的 ingress 设计
