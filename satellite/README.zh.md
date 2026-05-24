# Satellite Slot-Based Dynamic Link Prototype 中文说明

本文档对应当前主线的 slot-based dynamic link prototype，不再把旧的
candidate-link 模型作为长期主线。

## 当前原型范围

当前固定规模为：

- 3 个 `UT`
- 6 个 `ST`
- 3 个 `GW`
- 外加一套恢复后的 compile-time `B00 miniInternet`

其中 `ST` 分成两条轨道：

- `sat-a1`, `sat-a2`, `sat-a3`
- `sat-b1`, `sat-b2`, `sat-b3`

核心运行时控制都在
[controller.py](/home/seed/skyseed/satellite/controller.py:1)。
SEED 只负责创建基础容器和固定轨内链路。
在当前版本中，SEED 也负责创建 compile-time 的地面 `miniInternet`
结构，包括 `IX100-IX105`、transit AS、stub AS，以及 `GW` 的地面 `IX`
上联接口。

这个第一阶段 `skyseed` 镜像的命令解释规则是：

- 如果当前目录是 `/home/seed/skyseed`，把历史上的
  `satellite/...` 命令读成 `satellite/...`
- 如果当前目录是 `/home/seed/skyseed/satellite`，直接使用本地脚本名，
  例如 `python3 controller.py ...`

## 当前阶段边界

当前阶段现在只做三件收窄的事情：

- 恢复完整的 compile-time `B00 miniInternet`
- 保持 slot-based satellite runtime link model 可运行、可观察
- 在 example/runtime 层补上当前“已 attach 且受支持 `UT`”的
  `UT egress MPLS`，以及最小 selected-`GW` `NAT/general return path`

当前阶段明确不做以下内容：

- 不恢复旧的 `forwarding_state`、`candidate`、`snapshot` 主流程
- 不引入真实分布式 `MPLS` 控制面协议，例如 `LDP`、`RSVP`、`SR`
- satellite runtime side 不跑 `OSPF/iBGP/eBGP`
- 不保证 gateway/scenario 切换时的 active session continuity
- 不支持并发 runtime transaction 或 multi-writer mutation

因此当前的 compile-time 地面互联网、runtime 卫星动态链路层，以及
当前这条收窄的 `UT egress MPLS` dataplane，已经在同一个
example/runtime workflow 里并存；但它仍然不是通用 mobility 设计，
也不是并发控制面设计。

## 当前链路模型

- `UT-ST`：每个 `UT` 预留 2 个 access slots
- `GW-ST`：每个 `GW` 预留 2 个 gateway slots
- cross-plane `ST-ST`：两条轨道共享 3 个可复用 slots
- intra-plane `ST-ST`：固定 `front/back` 链路，compile-time 创建并长期存在

每个 slot 对应一个 Docker bridge network。

- `UT` / `GW` 固定端在 `--init-slot-runtime` 阶段预连接
- `ST` 端在运行时动态 connect/disconnect
- `miniInternet` 内部链路和 `GW` 的地面 `IX` 上联是 compile-time 固定结构，不由 slot runtime 管理

## 当前状态文件

- `configs/topology.json`：静态 topology inventory，只描述 fixed links 和 slot inventory
- `configs/link_state.json`：controller 维护的运行时 slot/link state

旧的 `attach_state.json`、`topology_state.json`、`forwarding_state.json`
不再是当前原型的主状态面。

注意：

- `link_state.json` 仍然只记录 satellite slot runtime 和 `fixed_isl`
- 它不会记录 `miniInternet` 内部链路

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

## 启动方式

```bash
python3 satellite/build_topology.py
cd satellite/output
docker compose up -d
python3 ../controller.py --init-slot-runtime
```

## 最小验证命令

查看当前 slot state：

```bash
python3 satellite/controller.py --show-link-state
```

运行只读的 `MPLS-0` capability probe：

```bash
python3 satellite/controller.py --probe-mpls
python3 satellite/controller.py --probe-mpls --probe-report satellite/configs/mpls_probe.json
```

这个 probe 不会加载宿主机模块，不会修改 `sysctl`，也不会安装路由。
它只检查：

- 宿主机侧 `mpls_router` 和 `mpls_iptunnel` 的 `modprobe` 可用性
- 宿主机侧 `net.mpls.platform_labels` 是否可见且为非零值
- 宿主机侧 `iproute2` 是否支持 `ip -f mpls route` 和 `encap mpls`
- 当前正在运行的 `ST/GW` 容器里，是否能看到且启用非零值的 `MPLS` 相关 sysctl，以及 Docker capability posture 是否满足

运行最小 `MPLS-1` static label lab：

```bash
python3 satellite/mpls_static_lab.py
```

清理这个 lab 安装的静态 `MPLS/IP` 路由与接口 `input` 开关：

```bash
python3 satellite/mpls_static_lab.py --cleanup
```

显式运行当前 `MPLS-2` controller-driven static `LSP` 动作：

```bash
python3 satellite/controller.py --install-static-mpls-lsp sat-a1:sat-b1:gw1
python3 satellite/controller.py --verify-static-mpls-lsp sat-a1:sat-b1:gw1
python3 satellite/controller.py --cleanup-static-mpls-lsp sat-a1:sat-b1:gw1
```

在这个阶段，`controller` 负责显式 static `LSP` intent，并且会在安装
Linux `MPLS` 路由前，先根据 `link_state.json` 验证当前 path 是否真实存在。
`mpls_static_lab.py` 继续保留为这些 controller 动作的 regression/smoke wrapper，
并可在需要时先补齐 runtime path。

这个 `MPLS-1` lab 当前只验证：

- `sat-a1 -> sat-b1 -> gw1`
- `push`
- `swap`
- `pop`
- Linux kernel `MPLS` dataplane / `LFIB` install / label switching / 端到端转发

它当前明确不做：

- 动态 path computation
- topology-driven `MPLS` orchestration
- handover-aware forwarding
- `LDP` / `RSVP` / `SR`
- 通用 `MPLS Layer/Service` API

运行当前 `MPLS-3` 的单 `UT` Internet egress controller 动作：

```bash
python3 satellite/controller.py --prepare-ut-egress-policy ut1
python3 satellite/controller.py --install-ut-egress-mpls ut1
python3 satellite/controller.py --verify-ut-egress-mpls ut1
python3 satellite/controller.py --cleanup-ut-egress-mpls ut1
```

其中：

- `--prepare-ut-egress-policy` 会在所有 candidate serving satellite 上一次性安装
  `static_guardrail` policy，包括 source-rule skeleton 和汇总后的
  internal/control/runtime guardrail prefixes；它应在
  `--init-slot-runtime` 之后执行，可重复执行且幂等
- `--install-ut-egress-mpls` 现在只安装 `dynamic_path` / `dynamic_lfib` /
  `dynamic_gateway_nat`，不再每轮重装几十条 exclusion routes
- `--dry-run` 会输出 plan 和 category/node 统计；`--timing` 会输出
  static/dynamic/verify/cleanup 的耗时汇总

运行当前已经固化的 `MPLS-3` regression/smoke wrapper：

```bash
python3 satellite/mpls_ut_egress_smoke.py
python3 satellite/mpls_ut_egress_smoke.py --cleanup
```

运行当前已经支持的“所有已 attach 且受支持 `UT`”的 controller 动作：

```bash
python3 satellite/controller.py --install-all-attached-ut-egress-mpls
python3 satellite/controller.py --verify-all-attached-ut-egress-mpls
python3 satellite/controller.py --cleanup-all-attached-ut-egress-mpls
python3 satellite/controller.py --verify-ut-egress-mpls ut1 --ping-target 10.160.0.71
```

运行当前 `MPLS-4` 第一阶段的 scenario controller 动作：

```bash
python3 satellite/controller.py --apply-scenario t0
python3 satellite/controller.py --verify-scenario t0
python3 satellite/controller.py --apply-scenario t1
python3 satellite/controller.py --verify-scenario t1
```

运行当前已经固化的 `MPLS-4` scenario regression/smoke wrapper：

```bash
python3 satellite/mpls_scenario_smoke.py --scenario t0
python3 satellite/mpls_scenario_smoke.py --scenario t1
python3 satellite/mpls_scenario_smoke.py --sequence t0,t1
python3 satellite/mpls_scenario_smoke.py --scenario t0 --all-ut
python3 satellite/mpls_scenario_smoke.py --scenario t1 --all-ut
```

运行当前 `MPLS-4` 第二阶段的长期串行 scenario loop runner：

```bash
python3 satellite/mpls_scenario_loop.py
python3 satellite/mpls_scenario_loop.py --rounds 4
python3 satellite/mpls_scenario_loop.py --interval 5 --max-rounds 100
python3 satellite/mpls_scenario_loop.py --sequence t0,t1 --rounds 0
python3 satellite/mpls_scenario_loop.py --all-ut --max-rounds 2
```

这个阶段的 `controller` 会：

- 从 `link_state.json` 自动识别 `ut1` 当前 serving satellite
- 读取当前 occupied `gw_access`
- 在 active satellite graph 上选择最少跳数 path
- 把这条 path 转成显式 `UT egress MPLS intent/profile`
- 把 Linux `MPLS/IP` route 下发成更自然的 `default Internet egress`
  dataplane：`UT` 只装默认路由到当前 serving satellite，而 serving
  satellite 会把来自该 `UT` 的 non-local traffic 放进专用 source-policy
  table，并结合 controller 从 `topology.json` 推导出的 local/internal
  exclusions 做默认外发分类
- 在当前 selected gateway 上安装最小 Linux `iptables` `SNAT/FORWARD`
  规则，让这个 gateway 充当当前 miniInternet 回包锚点

当前 `MPLS-3` 已经进入 controller-managed `UT egress intent` 阶段，
当前的成功条件已经调整为“`UT stable identity` 负责控制面/FEC 身份，
active access-link source/interface 负责 Linux dataplane realization”，因此：

- control-plane path selection / intent generation / label programming 已实现
- `ut1` 使用当前 active access-link `source/interface` 到 external target `10.154.0.129` 的 end-to-end ping 已跑通
- `ut2` 在当前已定义 scenario 下也已经可以通过 controller-managed `MPLS`
  path 访问 `10.154.0.129`
- 现在至少已经验证了一个非 `10.154.0.129` 的 miniInternet host：
  `ut1 -> 10.160.0.71` 可以通过同一套 `MPLS + GW NAT` dataplane 跑通
- `100.64.0.1/32 on dummy0` 仍然只是 `UT stable identity`，不是当前 Linux 业务 source address
- 当前 `UT egress` 的核心 forwarding 语义已经不再依赖
  external host prefix allowlist；controller 会从 `topology.json` 自动推导
  local/internal exclusion prefixes，把其余 `UT` 源流量都视为 default
  Internet egress，交给当前 selected gateway
- host `bridge netfilter` passthrough 现在是 runtime readiness 的一部分：
  `--init-slot-runtime` 会准备 `net.bridge.bridge-nf-call-{iptables,ip6tables,arptables}=0`，
  如果这组 host sysctl 不是 `0`，`--install-ut-egress-mpls` / `--verify-ut-egress-mpls`
  会明确失败
- `mpls_ut_egress_smoke.py` 会把当前成功路径固定为
  `ut1 -> sat-a1 -> sat-b1 -> gw1 -> 10.154.0.129`，
  顺序验证 `access-link -> push -> swap -> pop -> round-trip`，
  然后清掉 forwarding state，但保留 runtime topology attachment
- `--install-all-attached-ut-egress-mpls` / `--verify-all-attached-ut-egress-mpls`
  / `--cleanup-all-attached-ut-egress-mpls` 现在会面向当前 `link_state.json`
  里所有“已 attach 且受支持”的 `UT intent` 顺序执行
- 当前 all-UT scope 仍然是刻意收窄的：
  当前真实验证主路径只围绕 `t0/t1` 和 outbound Internet-egress baseline
- 当前 return path 已经从单目标静态 return route 提升为“最小 selected-GW NAT”：
  当前 selected gateway 会把流量 `SNAT` 到它自己的 bootstrap-side miniInternet IP
  （`10.65.21.2`、`10.65.22.2`、`10.65.23.2`），
  回包依赖 Linux conntrack/NAT reverse mapping
- `t0/t1` 切换导致 selected gateway 改变时，当前阶段仍然不保证 active session continuity
- `controller.py --apply-scenario t0|t1` 现在可以显式收敛一个 scenario，
  只通过串行 attach/release/connect 操作把 runtime topology 调整到目标状态，
  并再基于结果验证 `link_state.json`
- `mpls_scenario_smoke.py --sequence t0,t1` 现在可以在不重启容器的前提下，
  依次执行 `t0 -> cleanup forwarding -> t1 -> cleanup forwarding`，
  并在每一步重新安装/验证 `ut1` egress `MPLS`
- `mpls_scenario_smoke.py --scenario t0|t1 --all-ut` 现在可以对当前所有
  已 attach 且受支持的 `UT` 做同样的验证，并额外验证一个任意 miniInternet host
  目标（`10.160.0.71`）
- 真实 runtime 容器里的 `default Internet egress` 现在已经覆盖
  `ut1`、`ut2`、`ut3`，并且完成过一次严格的
  `cleanup all -> apply scenario -> reinstall all -> verify all` 闭环验证
- `single-UT cleanup` 现在会保留仍被其他已安装 `UT egress` plan 共享的
  `MPLS input` 接口，不再因为清一个 `UT` 就把共用 satellite / gateway
  ingress 接口直接关掉
- 当前串行 runtime 的真实耗时也已经记录下来，这些数字很直接地说明了后续为什么需要并行化的 runtime/controller update：
  `apply_scenario t1 = 44.78s`；一次严格的 all-UT
  `t1 -> cleanup all -> t0 -> install all -> verify all` 里，
  `cleanup_all=11.35s`、`apply_scenario t0=48.51s`、
  `install_all=39.98s`、`verify_all=16.17s`
- `mpls_scenario_loop.py` 现在会把同一套串行 scenario cycle 组织成 loop runner，
  每一轮都打印当前 scenario、selected gateway、selected path 和 labels
- 现在直接运行 `python3 satellite/mpls_scenario_loop.py`
  就会启动 continuous demo：
  默认无限循环、每轮之间 sleep 5 秒，也可以用 `--max-rounds` 限定轮数
- `mpls_scenario_loop.py --all-ut` 现在也已经提供出来，
  但当前最长的 repeated-transition soak 证据仍然集中在默认 ut1-only loop；
  如果要验证 all-UT 改动，建议先跑 all-UT smoke wrapper
- 每一轮仍然严格执行
  `cleanup forwarding -> apply scenario -> verify scenario -> install ut1 egress MPLS -> verify dataplane -> cleanup forwarding`
- 用户按 `Ctrl-C` 时，loop runner 会做 best-effort forwarding cleanup，
  打印最终 `link_state`，然后退出；
  如果中断恰好打在 in-flight runtime mutation 中间，建议再手动跑一次
  `--apply-scenario` 或 smoke wrapper，把 topology 明确收敛回目标 scenario
- 如果任一步 attach/release/connect/install/verify 失败，
  loop runner 会终止当前轮、做 best-effort forwarding cleanup、打印 `link_state`，
  然后直接退出，而不是继续带着不确定状态运行
- loop runner 现在还会在每轮成功后做第一版 residue/stability 观察：
  检查 forwarding cleanup、scenario/link_state 一致性、managed slot bridge occupancy，
  以及 satellite runtime interface 是否出现 stale residue；只要发现异常就停
- 当前 example/runtime 层已经有第一批 bounded soak 结果：
  10 轮 run 已完整成功；50 轮目标在人工停止前已经连续成功 44 轮，
  在这些已完成轮次里没有观察到 stale `LFIB`、stale IP route、
  `link_state` mismatch，或 satellite runtime interface residue
- 如果 `Ctrl-C` 恰好打在 in-flight runtime mutation 中间，
  当前 cleanup 仍然只是 best-effort forwarding cleanup，
  topology 可能停在 partial transition 状态；继续工作前应显式重跑
  `--apply-scenario` 或 smoke wrapper，把 topology 收敛回确定 scenario
- 这已经是最终持续 `t0 -> t1 -> ...` loop 的第一版 continuous demo/stability 实现；
  但当前仍然只有单 `UT`，还不是后台 topology watcher
- controller state mutation 目前仍然只能串行执行，不要并行跑
  `--attach-ut`、`--attach-gw`、`--connect-cross`、`--release-slot`
- 当前 runtime controller 应明确视为
  `single-writer / serialized-operation safe only`，
  不要同时开多个 controller 终端并发修改 runtime state

当前 correctness baseline 和推荐人工验收命令如下：

```bash
# 当前 attached scenario 下的 all-UT external egress
python3 satellite/controller.py --install-all-attached-ut-egress-mpls
python3 satellite/controller.py --verify-all-attached-ut-egress-mpls
```

当前默认的 correctness baseline 只保留 residential / `CGNAT-like`
`UT -> external` 路径：

- `UT -> serving satellite -> selected GW -> miniInternet host`
- 当前 selected `GW` 负责最小 `SNAT/FORWARD` realization，承接 outbound
  access 与 return traffic
- `external host -> UT` 不属于当前 baseline
- `UT -> UT` direct reachability 不属于当前 baseline
- 如果未来重新评估 `UT <-> UT`，应把它视为独立的
  operator-internal subscriber-routing/core 设计问题，而不是 public-inbound
  或 per-pair shortcut 扩展

当前推荐的人工验收命令仍然是：

```bash
python3 satellite/controller.py --prepare-ut-egress-policy ut1 --timing
python3 satellite/controller.py --apply-scenario t0
python3 satellite/controller.py --install-ut-egress-mpls ut1 --timing
python3 satellite/controller.py --verify-ut-egress-mpls ut1 --timing

python3 satellite/controller.py --cleanup-ut-egress-mpls ut1 --timing
python3 satellite/controller.py --apply-scenario t1
python3 satellite/controller.py --install-ut-egress-mpls ut1 --timing
python3 satellite/controller.py --verify-ut-egress-mpls ut1 --timing

# 当前 attached scenario 下的 all-UT external egress
python3 satellite/controller.py --install-all-attached-ut-egress-mpls
python3 satellite/controller.py --verify-all-attached-ut-egress-mpls
```

当前的 transition 目标只限于 `t0 <-> t1`：

- `t0` 用来验证当前 selected gateway/path 下的 outbound egress
- `t1` 用来验证 controller 能切换到新的 selected gateway/path
- 串行 runtime mutation 期间，持续 ping 可能短暂掉包；重点是新 forwarding
  state 安装完成后，对外连通性能够恢复

查看当前相关 Docker network 数量：

```bash
docker network ls --format '{{.Name}}' | grep -E '(access-slot|gateway-slot|cross-slot|^link-a|^link-b)'
```

查看某个容器里的 interface 数量：

```bash
docker exec ut1 ip -o link show | wc -l
docker exec gw1 ip -o link show | wc -l
docker exec sat-a1 ip -o link show | wc -l
```

触发一次 `UT-ST` 动态挂载：

```bash
python3 satellite/controller.py --attach-ut ut1:sat-a1
python3 satellite/controller.py --attach-ut ut1:sat-a1 --slot-name ut1-access-slot1
```

释放这个 `UT` slot：

```bash
python3 satellite/controller.py --detach-ut ut1
```

触发一次 `GW-ST` 动态挂载：

```bash
python3 satellite/controller.py --attach-gw gw1:sat-b1
python3 satellite/controller.py --attach-gw gw1:sat-b1 --slot-name gw1-gateway-slot1
```

释放这个 `GW` slot：

```bash
python3 satellite/controller.py --detach-gw gw1
```

触发一次 cross-plane 动态连接：

```bash
python3 satellite/controller.py --connect-cross sat-a1:sat-b1:left:right
python3 satellite/controller.py --connect-cross sat-a1:sat-b1:left:right --slot-name ab-cross-slot2
```

释放这个 cross-plane slot：

```bash
python3 satellite/controller.py --release-slot ab-cross-slot0
```

查看某个容器里的接口/IP：

```bash
docker exec sat-a1 ip -o addr show
docker exec ut1 ip -o addr show
docker exec gw1 ip -o addr show
```

## Runtime Demo

当前提供一个串行 handover demo 脚本：

`satellite/demo_runtime_handover.py`

示例：

```bash
python3 satellite/demo_runtime_handover.py --interval 5 --iterations 2
```

这个 demo 会在以下两组状态之间切换：

- `t0`：`ut1 -> sat-a1`，`gw1 -> sat-b1`，cross-plane `sat-a1 <-> sat-b1`
- `t1`：先释放 cross-plane，再 detach `ut1`、detach `gw1`，然后 `ut1 -> sat-a2`，`gw1 -> sat-b2`，cross-plane `sat-a2 <-> sat-b2`

每一步串行 runtime 操作后，脚本都会打印：

- 当前 active links
- 当前 occupied slots
- 当前执行的 attach/detach/connect/release 操作
- `link_state.json` 摘要
- 当前 `UT` / `GW` / cross-plane access IP 的简单连通性探测结果

这个 demo 刻意复用同一组 slot：

- `ut1-access-slot0`
- `gw1-gateway-slot0`
- `ab-cross-slot0`

因此这里观察到的 probe IP 是“slot peer IP”，不是“某颗卫星专属 IP”。
也就是说，handover 时变化的是 `binding` 和容器 interface，而不是 probe IP 本身。

注意：这个当前 demo 只验证 slot runtime 的 attach/detach/connect/release、
slot reuse、状态一致性和可视化观察方式。它不覆盖后面新增的
`UT egress MPLS` / selected-`GW` `NAT/general return path` workflow，
也不恢复旧 forwarding baseline。

## Controller 已实现的安全原则

- 每次 `docker network connect` 后，目标容器必须恰好新增 1 个 interface
- controller 只会 rename 它刚刚确认出来的新 interface
- 运行时验证失败时会回滚部分 connect
- 只有成功后才写 `link_state.json`
- cross-plane 连接采用“两端依次连接，第二端失败则回滚第一端”的策略
- 如果不指定 `--slot-name`，controller 会按 slot 名字排序后选择第一个空闲 slot

## Bootstrap 接口说明

- 某些容器中可能还能看到 SEED bootstrap 阶段留下的 `eth*` 接口，并且它们处于 `DOWN` 状态
- 这本身不代表 slot runtime 有 bug
- slot 校验关注的是 `docker network connect` 前后新增的 interface diff
- controller 关心的是“是否恰好新增了 1 个新接口”，而不是要求把所有历史 bootstrap `eth*` 都清空

## Routing 边界

- `miniInternet` 内部继续使用 SEED 的 `Routing + Ebgp + Ibgp + Ospf`
- `GW` 在地面 `IX` 上联侧继续使用 SEED 的 `eBGP` 语义
- `AS250` satellite side 和 `AS65000` 的卫星 runtime side 当前都明确从 `Ospf/Ibgp` 中屏蔽
- `UT-ST`、`GW-ST`、cross-plane `ST-ST`、以及当前固定轨内 `ISL`，仍由 controller 和 `link_state.json` 管理，不交给 SEED 自动计算卫星内部转发

## 观察建议

- `SEED map` URL：`http://127.0.0.1:8080/pro/map`
- 当前 map 应视为 Docker compiler 生成的编译期拓扑视图
- 在当前版本里，map 编译期视图应当能看到完整 `B00 miniInternet`、`AS250` satellite side、`AS65000` gateways
- 不要假设它会实时反映 runtime `docker network connect/disconnect` 的 slot 变化
- 运行时真相请以 demo 脚本输出和 `python3 satellite/controller.py --show-link-state` 为准
- 当前阶段没有稳定的 `UT -> ground host` ping 目标，因为 forwarding/MPLS 故意不在范围内
- 这个 demo 复用同一个 `UT` access slot，所以推荐从 `UT` 侧持续 ping 同一个 slot peer IP，并同时观察 `link_state.json` 里的 `binding.satellite_id` 变化：

```bash
while true; do
  date
  docker exec ut1 sh -lc 'ping -c 1 -W 1 10.90.11.1 >/dev/null 2>&1 && echo ut1-slot0 up || echo ut1-slot0 down'
  python3 satellite/controller.py --show-link-state | grep 'ut1-access-slot0'
  sleep 1
done
```

- 如果你想从 `GW` 侧观察 feeder handover，同样推荐持续 ping 同一个 slot peer IP，并观察 `binding.satellite_id`：

```bash
while true; do
  date
  docker exec gw1 sh -lc 'ping -c 1 -W 1 10.91.11.1 >/dev/null 2>&1 && echo gw1-slot0 up || echo gw1-slot0 down'
  python3 satellite/controller.py --show-link-state | grep 'gw1-gateway-slot0'
  sleep 1
done
```

- 如果你想观察 cross-plane handover，可以从 `sat-a1` 或 `sat-a2` 侧看同一个 cross-plane peer IP 的短暂 down/up，并配合查看 `ab-cross-slot0` 的绑定变化：

```bash
while true; do
  date
  docker exec sat-a1 sh -lc 'ping -c 1 -W 1 10.92.1.2 >/dev/null 2>&1 && echo cross-slot0-from-a1 up || echo cross-slot0-from-a1 down'
  docker exec sat-a2 sh -lc 'ping -c 1 -W 1 10.92.1.2 >/dev/null 2>&1 && echo cross-slot0-from-a2 up || echo cross-slot0-from-a2 down'
  python3 satellite/controller.py --show-link-state | grep 'ab-cross-slot0'
  sleep 1
done
```

- 这类观察的重点不是“IP 从 A 变成 B”，而是：
  - release/attach/connect 过程中 ping 可能短暂失败
  - 之后同一个 slot peer IP 会重新恢复连通
  - `link_state.json` 中的 `binding` 会从 `sat-a1/sat-b1` 切到 `sat-a2/sat-b2`

## MPLS-0 边界

下一阶段的第一步只是 capability probe。

- 不恢复旧 fake `forwarding_state`
- 还不安装 kernel `MPLS` route
- 还不打通 `UT -> miniInternet`
- 不引入 `LDP`、`RSVP` 或 distributed label signaling

`MPLS-0` 的目标只回答一个窄问题：

- 当前 host 加 Docker runtime，是否具备支撑后续 centralized Linux kernel `MPLS` dataplane 的基本能力

## Future Forwarding/MPLS Demo Plan

下面这组状态设计是后续 forwarding/MPLS baseline 阶段准备采用的循环切换方案。
它目前只是 plan，不代表当前已经实现 `UT -> miniInternet` 连通性。

`t0`:

- `ut1 -> sat-a1`
- `ut2 -> sat-a2`
- `gw1 -> sat-b1`
- `gw2 -> sat-a3`
- `ab-cross-slot0: sat-a1(left) <-> sat-b1(right)`
- `ab-cross-slot1: sat-a2(left) <-> sat-b2(right)`

`t1`:

- `ut1 -> sat-a2`
- `ut2 -> sat-a3`
- `gw1 release`
- `gw2 -> sat-b2`
- `ab-cross-slot0: sat-a2(left) <-> sat-b2(right)`
- `ab-cross-slot1: sat-a3(left) <-> sat-b3(right)`

后续 forwarding/MPLS 阶段的目标，是让 `UT stable identity address/prefix`
通过这些动态链路状态变化，仍然能够到达 `miniInternet` 中的 external host。
但这不属于当前阶段的交付范围。

## 设计文档

详细设计见
[docs/satellite_phase1_design.zh.md](/home/seed/skyseed/satellite/docs/satellite_phase1_design.zh.md:1)。
虽然文件名保留了历史命名，但内容已经改为描述当前的 Phase 2
slot-based prototype，并替代旧的 candidate-link 主叙事。

MPLS core integration 相关说明记录在
原始 `seed-emulator` 开发树中；这个第一阶段 `skyseed` 镜像暂未复制该文件。
