# SEED Emulator 扩展定向调查

> Historical record, retained under its original dates, parameters and source revisions.
> Commands and paths below describe that work; use the current guides for new operations.
> This record does not validate phase 4b-2.

> Non-authoritative provenance. Upstream API observations may become stale;
> current Skyseed contracts are not derived from this file by default.

## 0. 调查边界与版本

本文件只调查当前本地 SEED 实现中与 Skyseed v2 第一阶段静态 Emulator 直接相关的扩展边界。没有调查旧 Skyseed、Simulator、Topology、Planner、BPF 或 MPLS，也没有修改或运行 SEED。

本次事实来源固定为：

- 本地目录：`/home/seed/seed-emulator-upstream-dev`
- Git 分支：`master`
- Git commit：`61acdf4c9699b25b06843ec4aad747108b05924e`
- commit 时间与标题：`2026-06-22T16:28:06+08:00`，`removed pdf`
- Python 包声明版本：`seedemu 0.0.7`，见 `setup.py:6-15` 的 `setuptools.setup()`。
- 调查开始前执行了 `git pull --ff-only`，结果为 `Already up to date.`；工作树无改动。

下文的 `SEED_ROOT` 均指上述本地目录，所有行号均对应上述 commit。

## 1. 相关文件和关键符号索引

| 主题 | 文件与关键符号 |
| --- | --- |
| 节点角色 | `seedemu/core/enums.py:21-42`，`NodeRole` |
| 基础节点与 Router | `seedemu/core/Node.py:209-375`，`Node`；`seedemu/core/Node.py:1131-1402`，`Router`、`RouterExtension` |
| AS 节点集合 | `seedemu/core/AutonomousSystem.py:50-91`，`AutonomousSystem`；`:474-543`，`registerNodes()`、`configure()`；`:594-688`，节点工厂与查询 |
| Registry | `seedemu/core/Registry.py:4-78`，`Registrable`；`:80-173`，`Registry` |
| Base 生命周期 | `seedemu/layers/Base.py:83-118`，`Base.configure()`、`Base.render()` |
| SCION 扩展先例 | `seedemu/core/ScionAutonomousSystem.py:128-196`，`ScionAutonomousSystem`；`:319-329`，`createControlService()` |
| 路由能力 | `seedemu/layers/Routing.py:163-221`、`:439-575`，`Routing` |
| OSPF/iBGP/eBGP | `seedemu/layers/Ospf.py:14-199`；`seedemu/layers/Ibgp.py:19-136`；`seedemu/layers/Ebgp.py:32-170`、`:364-441` |
| Docker 编译 | `seedemu/compiler/Docker.py:460-560`，`Docker`；`:860-972`，节点 metadata；`:1014-1103`，节点名与网络；`:1227-1262`、`:1462-1553`，编译主路径 |
| 可视化后端 | `tools/InternetMap2/backend/src/api/v1/main.ts:24-40`，`getContainers()`；`tools/InternetMap2/backend/src/utils/seedemu-meta.ts:55-82`，`ParseNodeMeta()` |
| 可视化前端 | `tools/InternetMap2/frontend/src/utils/map-datasource.ts:321-412`，运行时图数据；`tools/InternetMap2/frontend/src/utils/tools.ts:198-273`，上传 compose 数据 |

## 2. Node 类型关系与默认行为

### 2.1 实际类型关系

当前可部署节点的核心关系是：

```text
Printable  Registrable  Configurable  Vertex  Customizable
     \          |             |          |          /
                         Node
                           |
                         Router
```

证据：

- `Node` 明确继承 `Printable, Registrable, Configurable, Vertex, Customizable`（`seedemu/core/Node.py:209-214`）。这些父类是能力/协议基类，不是另一种可直接部署的轻量节点。
- `Router(Node)` 是核心中的具体节点子类（`seedemu/core/Node.py:1131-1158`）。本次范围内没有发现独立的 `Host` 类；普通 host 是 `Node(name, NodeRole.Host, asn)`，由 `AutonomousSystem.createHost()` 直接构造（`seedemu/core/AutonomousSystem.py:661-671`）。
- 现有 `NodeRole` 只有 Host、Router、BorderRouter、ControlService、RouteServer 和 OpenVpnRouter，没有 Satellite 或 GroundStation（`seedemu/core/enums.py:21-42`）。
- SCION control service 也不是更轻的节点类，而是 `Node(..., NodeRole.ControlService, ...)`（`seedemu/core/ScionAutonomousSystem.py:319-329`）。

结论：当前没有比 `Node` 更基础且仍满足 SEED 节点编译合同的具体类型。可以复用其父类拼出新类，但那等于重新定义节点合同，不是已有的正式轻量节点扩展点。

### 2.2 `Node` 自身引入的内容

`Node.__init__()` 创建空接口、文件、命令和端口集合，默认 `BaseSystem.DEFAULT`，并安装一组通用软件；它本身允许初始接口列表为空（`seedemu/core/Node.py:247-290`）。默认软件包括 shell/诊断/网络工具，如 `zsh`、`curl`、`iproute2`、`tcpdump`、`jq` 等（`seedemu/core/Node.py:21`）。`BaseSystem.DEFAULT` 是 `SEEDEMU_BASE`，而 router image 是单独的 `SEEDEMU_ROUTER`（`seedemu/core/BaseSystem.py:5-24`）。

`Node` 构造器没有默认安装 Web/BGP 等业务 service，也没有默认路由；它只准备通用容器节点状态与软件集合。具体 service 由相应 Service/Layer 安装，路由行为则由后述 Routing/Ospf/Ibgp/Ebgp Layers 引入。

`Node.configure()` 的网络行为是：

1. 处理 `joinNetwork()` 留下的 pending network；
2. 依次解析 AS-local、IX、SEED service network；
3. 真正创建接口；
4. Router 子类加入 IX 时被标记为 border router。

对应实现位于 `seedemu/core/Node.py:308-375`，其中网络解析为 `:323-341`，IX 上的 Router 判定为 `:331-335`。若 pending network 和 cross-connect 都为空，方法不会因为缺少接口而失败；它只要求节点尚未配置且 ASN 不为 0（`:317-320`）。

`Node` 还承载大量容器构建/运行配置，而不只是领域身份：软件、文件、build/start command、端口、volume、environment、sysctl option 等字段见 `seedemu/core/Node.py:216-245`。因此把完整 `Node` 暴露给 Simulator 会把容器实现细节带过 Emulator–Simulator 边界。

### 2.3 `Router` 自身与 Layer 的分工

`Router` 构造器只是在 `Node` 上增加 loopback、border 状态、BGP/路由 backend 状态和 extension 容器，并默认选择 `bird` backend（`seedemu/core/Node.py:1139-1158`、`:1281-1303`）。它没有仅因实例化就安装或启动路由 daemon。

真正的 daemon、loopback 和路由配置由 `Routing` Layer 引入：

- `Routing` 的职责说明明确包括安装 BIRD、设置 kernel/device protocol、给 host 加默认路由（`seedemu/layers/Routing.py:163-176`）。
- BIRD/FRR 安装和 router base image 切换在 `Routing._installBird()`、`_ensureRouterBaseSystem()`、`_installFrr()`（`:197-221`）。
- 对每个 registry type 为 `rnode` 的对象，`Routing.configure()` 要求它是 `Router`，创建 `dummy0` loopback、设置 label、安装 backend，并断言至少一个接口（`:439-483`，特别是 `:451-478`）。
- `Routing.render()` 才把 OSPF/BGP intent 渲染为 backend 配置（`:511-529`）。

`RouterExtension` 提供类似组合的扩展委派，但只能通过 `Router.installExtension()` 安装，且扩展持有 Router/Node（`seedemu/core/Node.py:1254-1269`、`:1386-1402`）。它不是任意 `Node` 的通用 capability 系统，因此不能直接用它让普通 Node 获得一小部分 Router 能力。

### 2.4 Satellite 与 GroundStation 的可行派生点（不作最终选择）

- Satellite 从 `Node` 派生是最小现有对象复用：保留 SEED 的容器配置合同，不引入 Router 状态；但新 registry type/role 仍需适配所有固定类型消费者。
- Satellite 直接使用 `NodeRole.Host` 并注册成 `hnode` 可减少扩展点修改，但会被正式地解释为 Host，并在存在 `Routing` Layer 时触发“host 必须恰有一个接口并找到网关”的约束，见 `seedemu/layers/Routing.py:542-575`。这与孤立卫星合同冲突。
- GS 从 `Router` 派生或直接使用 Router，可原样进入 IX、border-router 和 eBGP 路径；代价是接受完整 Router 状态，而且只要安装 `Routing` Layer 就接受 daemon、loopback 和至少一个接口的行为。
- GS 从 `Node` 派生可避免完整 Router 继承；代价是现有 `Routing`、`Ibgp`、`Ebgp` 都以 registry type/`Router` API 为合同，需要专门的、显式设计的地面路由 capability 或 Layer，不能靠现有 `RouterExtension` 无修改完成。

## 3. AS、Registry 与节点发现机制

### 3.1 Registry 本身是通用的，消费者不是

`Registry` 用 `(scope, type, name)` 三元组保存 `Registrable`，`register()` 不检查固定节点类型或 `Node` 子类（`seedemu/core/Registry.py:80-109`）；`getByType()` 和 `getAll()` 也是普通字符串过滤/遍历（`:135-158`）。所以 Registry 容器本身允许 `satnode`、`gsnode` 等新 type。

但 `Registrable.doRegister()` 每次注册会重写 registry info 并清空 `_attrs`（`seedemu/core/Registry.py:24-35`）。现有 border router 同时以 `rnode` 和 `brdnode` 注册（`seedemu/core/AutonomousSystem.py:537-543`），因此对象自己的 `getRegistryInfo()` 最后会反映后一次注册；以 `_attrs` 承载跨注册的领域 metadata 也存在被清空的风险。

### 3.2 `AutonomousSystem` 只拥有两类节点集合

`AutonomousSystem` 只有 `__routers`、`__hosts`、`__nets`（`seedemu/core/AutonomousSystem.py:57-68`，初始化见 `:71-91`）。标准生命周期如下：

- `registerNodes()` 将 network、host、router 分别注册为 `net`、`hnode`、`rnode`（`seedemu/core/AutonomousSystem.py:474-506`）。
- `configure()` 只遍历 host 和 router，并在 router 配置后补充 `brdnode` 注册（`:523-543`）。
- 工厂/查询也分别固定为 `createRouter()/getRouters()` 和 `createHost()/getHosts()`（`:594-688`）。

`Base.configure()` 先调用每个 AS 的 `registerNodes()`，再配置 IX，最后调用每个 AS 的 `configure()`（`seedemu/layers/Base.py:83-99`）。这说明“被 AS 拥有”不会自动使对象被发现；自定义集合必须明确参加 register、option inheritance 和 configure 生命周期。

### 3.3 现有直接参考：SCION AS

`ScionAutonomousSystem(AutonomousSystem)` 自己保存 `__control_services`（`seedemu/core/ScionAutonomousSystem.py:128-152`），覆盖：

- `registerNodes()`：先调用父类，再注册 `csnode`（`:173-180`）；
- `configure()`：先调用父类，再配置 control service（`:183-192`）；
- `createControlService()`：由 AS 负责批量集合中的单节点工厂（`:319-329`）。

这是“AS 子类 + 专用集合 + 覆盖生命周期”的直接先例。`Base.setAutonomousSystem()` 允许注入一个已有的 AS 子类实例（`seedemu/layers/Base.py:164-171`）。但 SCION 先例也表明仅扩展 AS 不够：下游 Base、option、Compiler 等仍需认识 `csnode`。

### 3.4 固定类型假设的位置

以下正式消费者硬编码了节点 type：

- `Base.render()` 只处理 `rs/rnode/hnode/csnode`（`seedemu/layers/Base.py:101-118`）。
- `AutonomousSystem.inheritOptions()` 只处理 `rnode/hnode/csnode/rsnode/rs`（`seedemu/core/AutonomousSystem.py:508-516`）。
- `DynamicConfigurable._prepare()` 同样只处理固定类型（`seedemu/core/Configurable.py:51-68`）。
- Docker software grouping 只处理 `rnode/csnode/hnode/snode/rs`（`seedemu/compiler/Docker.py:676-733`）。
- Docker compile 主循环也只编译 `rnode/csnode/hnode/rs/snode`（`seedemu/compiler/Docker.py:1462-1507`）。
- `Routing`、`Ospf`、`Ibgp`、`Ebgp` 各自按 `rnode`/`brdnode`/`hnode` 等固定 type 工作，见第 5 节。

因此，自定义 Satellite/GS 被 AS、Emulator、Layer 和 Compiler 正常发现至少需要满足：

1. 是 `Registrable`，且提供 Compiler 使用的 `Node` 合同；
2. 由 AS/Layer 在 Base 配置期注册，并参与 configure；
3. 明确是否参与 option inheritance 和 `Base.render()`；
4. Compiler 的 software grouping、compile dispatch、role metadata、node naming 都能处理其 type/role；
5. 需要路由能力时，被对应 Layer 明确纳入，而不是期望 Registry 自动发现；
6. 可视化所需 `nodename/asn/role` labels 能被生成。

### 3.5 批量创建的归属惯例

基础节点工厂属于 AS；例如 `createRouter()`、`createHost()`（`seedemu/core/AutonomousSystem.py:594-671`）。辅助函数可以循环调用 AS 工厂：`createHostsOnNetwork()` 就属于 utility/builder 风格，但实际所有权仍在 AS（`seedemu/utilities/Makers.py:41-79`）。因此可行边界包括：由扩展 AS 提供单节点工厂和集合，Skyseed Builder 只编排批量创建；或由专用 Layer/Builder 自有集合并直接注册。后者会绕开 AS 原生查询，应视为明确架构选择。

## 4. 无接口、孤立节点的编译与启动路径

### 4.1 可静态确认的生命周期

对一个已经放进 AS host/router 集合、但从未调用 `joinNetwork()` 的节点：

| 阶段 | 结果 | 证据 |
| --- | --- | --- |
| AS 注册 | 会进入 Registry | 注册发生在节点 configure 前，且不检查接口；`AutonomousSystem.registerNodes()`，`seedemu/core/AutonomousSystem.py:474-506` |
| `Node.configure()` | 单独看可以通过 | 空 pending networks 不触发网络断言；仅有 configured/ASN 断言，`seedemu/core/Node.py:308-375` |
| `Base.render()` | 对已知 type 可以通过 | 空接口只生成空 `/ifinfo.txt`；遍历见 `seedemu/layers/Base.py:101-118` |
| `Routing.configure()` 中的 `rnode` | 失败 | 明确断言 router 至少一个接口，`seedemu/layers/Routing.py:451-478` |
| `Routing.render()` 中的 `hnode/csnode` | 失败 | 明确断言 host 恰有一个接口，`seedemu/layers/Routing.py:542-545` |
| Docker `_compileNode()` | 失败 | `_getComposeNodeName()` 无条件读取 `getInterfaces()[0]`，`seedemu/compiler/Docker.py:1014-1027`；调用点 `:1227-1262` |
| Docker 的 `hnode` 后处理 | 也会失败 | 再次无条件读取第一个接口，`seedemu/compiler/Docker.py:1484-1493` |

最先稳定可复现的 stock Docker 阻塞是 `_getComposeNodeName()` 的 `IndexError`。即使自定义 `namingScheme` 不包含 `{primaryIp}`，format 参数仍会先计算 `node.getInterfaces()[0].getAddress()`（`seedemu/compiler/Docker.py:1019-1025`），所以改命名模板不能绕过。

### 4.2 SEED 是否自动添加默认网络

对普通 SEED Node，`Docker._getComposeNodeNets()` 只遍历已有接口，没有 fallback/default network（`seedemu/compiler/Docker.py:1065-1103`）。compose service 模板始终输出 `networks:` 段（`:128-144`），但 stock compiler 会在最终写出 compose 前因第一个接口访问而失败。

`attachCustomContainer(asn < 0)` 的确会省略 network entry，让 Docker 使用 default network（`seedemu/compiler/Docker.py:1620-1663`），但这是预构建 custom container 的专用 API，不是普通 Node 的路径，不能作为孤立 Satellite 的隐式 fallback。Internet Map 本身正是通过 custom-container 路径附加（`:1509-1514`、`:1600-1617`）。

因此当前可确认结论是：SEED 不会为普通无接口 Node 主动创建兼容网络或假链路；当前编译器也根本不能生成该节点的 compose service。若未来修复 compiler 后，`networks:` 为空时 Compose 的 schema/运行时究竟创建 default network、接受 null，还是拒绝，必须通过后续最小真实实验确认，不能由当前不可达模板推断。

### 4.3 与网络无关的最低配置

静态代码显示，至少还需要：

- ASN 非 0（`Node.configure()`，`seedemu/core/Node.py:317-320`）；
- Compiler 可识别的 registry type 和 `NodeRole`；未知 role 会在 `_nodeRoleToString()` 断言失败（`seedemu/compiler/Docker.py:958-972`）；
- 可选择的 `BaseSystem`/Docker image 以及 Node 基础构建合同（`seedemu/core/BaseSystem.py:5-24`；`seedemu/compiler/Docker.py:1125-1190`）；
- 若需要 Base options（包括 forwarding 默认值），节点 type 必须参与 option prepare/inherit，或新扩展明确提供等价路径（`seedemu/core/Configurable.py:51-68`）。

这些条件仍不足以让零接口节点通过现有 Docker Compiler，因为第一个接口访问是独立硬阻塞。

## 5. GroundStation 地面侧能力来自哪里

### 5.1 加入普通 Network 或 IX

标准路径是由 AS 创建 network/router，再由 router 调用 `joinNetwork()`。仓库 helper 展示了两种连接：内部 network 由 `AS.createNetwork()` 创建，IX 名称为 `ixN`，两者都通过 `Router.joinNetwork()` 排队（`seedemu/utilities/Makers.py:19-37`、`:96-107`）。

配置期 `Node.configure()` 先查 AS-local network，再查 `ix` scope（`seedemu/core/Node.py:323-339`）。Router 子类加入 IX 时自动 `setBorderRouter(True)`（`:331-335`），随后 AS 又把它注册为 `brdnode`（`seedemu/core/AutonomousSystem.py:537-543`）。这就是 eBGP/route-server 查找 border router 的标准入口。

### 5.2 行为归属

| 能力 | 来源 |
| --- | --- |
| 接口与网络加入 | `Node.joinNetwork()`/`Node.configure()`；`seedemu/core/Node.py:308-375`、`:552-567` |
| Router 状态/API | `Router`；loopback、border、backend、BGP intent/extension，`seedemu/core/Node.py:1131-1384` |
| IPv4 forwarding sysctl | Base 的 `SysctlOpts` option；`Base.getAvailableOptions()` 在 `seedemu/layers/Base.py:47-50`，默认 `net.ipv4.ip_forward=True` 在 `seedemu/options/Sysctl.py:8-24`，Docker 将 runtime sysctl 写入 compose，`seedemu/compiler/Docker.py:1264-1288` |
| 路由 daemon、loopback、router image | `Routing` Layer；`seedemu/layers/Routing.py:163-221`、`:439-483` |
| host 默认路由 | `Routing.render()`；`seedemu/layers/Routing.py:542-575` |
| OSPF 接口 intent | `Ospf` Layer，只扫描 `rnode`；`seedemu/layers/Ospf.py:14-24`、`:171-196` |
| iBGP sessions | `Ibgp` Layer，只取 `rnode`；`seedemu/layers/Ibgp.py:19-35`、`:103-136` |
| eBGP sessions | `Ebgp` Layer，依赖 Routing，并从 `rnode/brdnode` 选 Router；`seedemu/layers/Ebgp.py:32-55`、`:146-170`、`:364-438` |

`Ebgp` 只记录 BGP intent，注释明确由 Routing 决定最终渲染成 BIRD 或 FRR（`seedemu/layers/Ebgp.py:88-96`）。所以仅有 Router 或仅安装 Ebgp 都不等价于可工作的 BGP daemon。

### 5.3 可组合或裁剪的边界

现有可用裁剪点包括：

- `Ospf.maskAsn()` 可让整个 AS 不参加 OSPF（`seedemu/layers/Ospf.py:101-119`、`:171-179`）。
- `Ibgp.maskAsn()` 或 AS `setIbgpMode("disabled")` 可关闭整个 AS 的 iBGP（`seedemu/layers/Ibgp.py:69-90`、`:103-116`；`seedemu/core/AutonomousSystem.py:93-120`）。
- `Router.disableControlPlane("ibgp")` 可排除单个 Router（`seedemu/core/Node.py:1187-1208`，使用点 `seedemu/layers/Ibgp.py:92-120`）。
- AS BGP scope 可选 all-router 或 edge-only（`seedemu/core/AutonomousSystem.py:128-168`、`:263-285`）。

这些裁剪只影响协议参与，不移除 Router 的结构状态，也不阻止 `Routing` 安装 daemon、loopback 和 router image。因此“GS 需要地面转发，但不需要普通内部路由”有两类真实选项：

1. 使用 Router/rnode，安装 Routing，并显式 mask OSPF/iBGP，只保留所需地面/eBGP 能力；集成成本低，但对象与运行时能力偏重。
2. 使用 Node 派生的 GS，复用 Base/sysctl/interface 能力，由新的专用 Layer 组合所需地面路由；职责更精确，但现有 Routing/Ebgp 的固定 `rnode`/Router 合同不能直接复用，需要正式扩展而不是特殊条件。

## 6. Docker Compiler 的静态元数据扩展方式

### 6.1 Compiler 如何取得节点

Docker Compiler 直接遍历 `emulator.getRegistry().getAll()`，先编译 `net`，再按固定 registry type dispatch 节点（`seedemu/compiler/Docker.py:1462-1507`）。它不是通过 AS 查询实体，也不会基于 `isinstance(Node)` 自动编译自定义 type。

节点目录、Dockerfile、service、名称、网络和 labels 分别由 `_compileNode()`、`_computeDockerfile()`、`_getComposeNodeName()`、`_getComposeNodeNets()`、`_getNodeMeta()` 生成（`seedemu/compiler/Docker.py:860-956`、`:1014-1103`、`:1125-1190`、`:1227-1262`）。

### 6.2 可承载静态属性的现有机制

| 机制 | 是否进入 Docker output | 适合用途与限制 |
| --- | --- | --- |
| `Node.setLabel()` | 是 | `_getNodeMeta()` 把所有 Node labels 写成 `org.seedsecuritylabs.seedemu.meta.<key>` compose labels，`seedemu/compiler/Docker.py:935-939`。适合需被容器/可视化读取的扁平静态 metadata。 |
| display name/description/classes | 是 | 由 `_getNodeMeta()` 明确序列化，`seedemu/compiler/Docker.py:917-933`。 |
| interface/network | 是 | 变成 `net.i.name/address` labels，`:940-954`。 |
| `Registrable.setAttribute()` | 通常否 | 通用进程内扩展状态见 `seedemu/core/Registry.py:45-78`；Docker 只读取自己约定的内部属性，没有通用 attribute 序列化，而且重复注册会清空 `_attrs`。 |
| `Node.setGeo()` / `setNote()` | 否 | API 存在于 `seedemu/core/Node.py:967-1005`，但 `_getNodeMeta()` 没有读取。不能假定轨道/位置会自动进入 output。 |
| files/software/build/start command | 进入镜像或启动脚本 | `_computeDockerfile()` 使用这些字段，`seedemu/compiler/Docker.py:1125-1190`；这是容器构建/运行配置，不是实体 metadata。 |
| environment/sysctl options | 进入 compose/.env 或启动脚本 | Docker runtime/build 配置；例如 sysctl 路径 `seedemu/compiler/Docker.py:1192-1225`、`:1264-1300`。不应作为 Emulator–Simulator 领域合同。 |

若轨道、位置、稳定标识只需写入 output，现有最直接机制是 namespaced Node labels；但其值最终被格式化进 YAML 字符串，适合稳定、扁平、可序列化值，不代表它应成为内存中的主模型。

### 6.3 与只读 Simulator entity view 的边界

本次审查范围内没有发现 SEED 提供独立的、面向 Simulator 的只读 entity view。Registry 返回实际可变对象（`seedemu/core/Registry.py:151-158`），`Node` 又同时承载容器文件、软件、命令、端口和配置状态（`seedemu/core/Node.py:216-245`）。

因此对 Skyseed v2 的直接约束是：静态实体模型应由 Emulator 持有；只读 view 应从领域对象投影出 Simulator 真正需要的稳定 ID、类型、轨道/位置等字段。需要出现在容器 output/可视化中的那一小部分再投影为 SEED labels。完整 `Node`、Registry 对象或生成后的 compose service 都不应成为 Emulator–Simulator 合同。

## 7. SEED 可视化的节点发现方式

### 7.1 启动与数据源

`Docker` 默认 `internetMapEnabled=True`、端口 8080（`seedemu/compiler/Docker.py:495-550`），使用镜像 `handsonsecurity/seedemu-internetmap:2.0`（`:17`），生成的 map service 挂载 `/var/run/docker.sock`（`:189-196`）。因此随 output 启动的正式路径是：

```python
docker = Docker(internetMapEnabled=True)
emu.compile(docker, "./output", override=True)
```

随后在 output 中执行：

```bash
docker compose build
docker compose up -d
```

当前 InternetMap2 自身 README 也给出“先启动 emulation，再启动 map container”的独立方式，并列出 `/pro/map` 和 `/pro/dashboard`（`tools/InternetMap2/README.md:41-64`）。旧用户手册仍写 `/map.html`（`docs/user_manual/visualization.md:9-26`），而 README 第 59 行还使用已经与当前构造器不一致的 `clientEnabled` 名称；当前代码事实应以 `Docker(internetMapEnabled=...)` 和 `/pro/map` 为准，但实际发布镜像路由仍需真实验证。

运行时后端从 Docker socket 调用 `docker.listContainers()`，给每个容器解析 labels，并且只要求解析出的 `nodename` 非空；它不要求容器属于某个 network（`tools/InternetMap2/backend/src/api/v1/main.ts:24-40`）。未传 `all: true`，所以按 Docker API 语义该列表默认只包含运行中的容器；停止的节点不会继续作为“停止状态节点”保留在图中。

节点类型来自 compiler 生成的 `role` label；运行状态不是静态 label，而是 `docker.listContainers()` 返回的 `Dockerode.ContainerInfo` 中的实时 `State/Status` 等字段。可视化的 `SeedContainerInfo` 直接扩展该 Docker 类型并附加解析后的 metadata（`tools/InternetMap2/backend/src/utils/seedemu-meta.ts:29-36`）。因此类型属于编译期 metadata，状态属于 Docker runtime observation。

label parser 初始化 `nets: []`，再按可选 `net.i.*` 填充；`name/role/asn` 都来自 labels（`tools/InternetMap2/backend/src/utils/seedemu-meta.ts:55-82`）。Dashboard 直接请求容器列表，类型显示 `role`，IP 列遍历可能为空的 `nets`（`tools/InternetMap2/frontend/src/view/dashboard/index.vue:76-95`、`:165-189`）。

### 7.2 无接口节点是否能显示

运行时 map 的边与点是分开建立的：

- edge 只从 `node.NetworkSettings.Networks` 生成（`tools/InternetMap2/frontend/src/utils/map-datasource.ts:321-360`）；
- vertex 独立遍历所有后端返回的 `_nodes` 并加入图（`:363-412`）。

因此从仓库中的 InternetMap2 源码静态判断：一个已经运行、具有 `nodename/asn/role` labels、但 Docker network 集合为空的容器，会成为没有边的 vertex；图模型不要求用假网络或假链路才能显示它。

但“上传 docker-compose.yml”是另一条数据路径。它的 `isValidNode()` 明确要求至少一个 `org.seedsecuritylabs.seedemu.meta.net.*` label（`tools/InternetMap2/frontend/src/utils/tools.ts:198-221`），否则以 `No network configuration` 跳过（`:245-273`）。所以上传式静态地图目前不能显示零网络节点，除非后续适配该前端路径。

当前第一阶段计划使用实际启动容器后的 SEED 可视化，应关注运行时路径。其现实阻塞仍在 Docker Compiler：零接口普通 Node 在容器生成前已失败，见第 4 节。

### 7.3 自定义 Satellite/GS 类型的适配点

- Compiler 必须给它们输出 `nodename`、`asn`、`role` labels；目前 role 根据固定 registry type 写死（`seedemu/compiler/Docker.py:868-915`）。
- `_nodeRoleToString()` 也只认识现有 `NodeRole`（`seedemu/compiler/Docker.py:958-972`），新 enum 需要命名适配。
- InternetMap2 parser 的 `role` 是任意字符串，不需要枚举修改（`tools/InternetMap2/backend/src/utils/seedemu-meta.ts:11-20`、`:67-80`）。
- 前端只把 `Router`/`BorderRouter` 画成 dot，其他 role 都画成 hexagon（`tools/InternetMap2/frontend/src/utils/map-datasource.ts:367-389`）。所以 Satellite/GS 在最低适配下可以出现，但会使用通用非 Router 形状；若需要专用样式/筛选，才需要前端适配。

## 8. 可行设计选项及代价

本节只列出由当前代码支持的选项，不替第一阶段选择最终方案。

### 选项 A：Satellite 是 `Node` 子类，但注册为现有 `hnode`

优点：AS、Base、Docker 和可视化的大部分发现路径无需新增 registry type。

代价：身份被解释为 Host；Compiler role label 也是 Host；安装 `Routing` 时零接口必然失败；Docker 自身仍需修复零接口索引；未来 Host 语义可能渗入 Satellite。

### 选项 B：Satellite 是 `Node` 子类，使用专用 `satnode` 和专用 role

优点：领域语义清晰，能从一开始区分 Satellite，避免 Host 行为的隐式继承。

代价：需要扩展 AS 集合/生命周期、Base render/option type 集合、Docker grouping/dispatch/metadata/naming，以及可能的其他 Layer 扫描。SCION AS 是生命周期参考，但不能消除下游适配工作。

### 选项 C：Satellite 由专用 Layer/Builder 持有并直接注册

优点：不必修改基础 AS 的私有集合，可把 constellation 批量创建放在 Skyseed 自己的对象中。

代价：`AutonomousSystem.get*()` 无法查询它；必须自己保证 register/configure 顺序和 options；固定类型 Compiler/Layer 仍要适配。Registry 的通用性只解决存储，不解决发现合同。

### 选项 D：GS 使用 `Router`/`rnode`

优点：标准 local network/IX 接口、border-router、eBGP 和 BIRD/FRR 路径可直接使用；可用 OSPF/iBGP mask 控制协议范围。

代价：继承完整 Router 状态；`Routing` 会安装完整 daemon、router image 和 loopback；mask 不能把它变回轻量节点。适合确认“GS 就是地面路由器”后采用，不适合仅因需要一个地面接口就默认采用。

### 选项 E：GS 使用 `Node` + 专用地面路由 capability/Layer

优点：可精确限定静态地面接口、forwarding 与批准的协议，避免普通内部路由行为。

代价：现有 `Ebgp` 和 `Routing` 不能直接组合到普通 Node；需要定义新的正式 capability 与 compiler/layer 合同，工作量更高。现有 `RouterExtension` 只服务 Router，不能解决该问题。

### 选项 F：在 SEED 上新增更轻的通用基础节点

优点：可把“可编译容器节点”的最小合同与当前功能丰富的 `Node` 分开，Satellite/GS 再按能力组合。

代价：这是最广的上游变更，需要重新覆盖 `Node` 当前承担的 Registrable、Configurable、Vertex、Customizable、构建配置和 Compiler API；现有 Compiler 直接依赖大量 Node 方法。若第一阶段只需 Satellite/GS，两种专用子类/adapter 可能比重塑核心更小。

## 9. 仍需最小真实实验确认的问题

以下内容不能仅凭当前静态代码可靠确认，应在设计选项确定后单独进行最小实验，且不得通过假网络“跑通”：

1. 修复/扩展零接口编译合同后，生成 `networks:` 为空或显式无 network 的 service 是否通过当前 Docker Compose 版本的 schema 校验，以及运行时是否被自动加入 project default network。
2. 如果目标合同要求“除 loopback 外没有网络接口”，Docker 本身强制存在的 `lo`、可能的 default-network `eth0` 与领域所称“无卫星网络接口”如何定义和验收。
3. `handsonsecurity/seedemu-internetmap:2.0` 的实际发布镜像是否与仓库 `tools/InternetMap2` 当前源码一致；特别是 `/pro/map` 路由和零边 vertex 行为。
4. 真实运行时 `docker.listContainers()` 返回的零网络容器数据结构，以及 map/dashboard 是否无异常显示；静态源码支持该结论，但尚无运行证据。
5. 自定义 Satellite/GS role 只使用通用 hexagon 是否满足第一阶段识别需求，还是必须加入专用样式、图例或筛选。
6. 若 GS 采用 Router 路径，mask 掉 OSPF/iBGP 后，实际生成的 BIRD/FRR 配置和 forwarding 行为是否恰好满足已确认的地面合同。
7. 当前 compiler 构造器注释称 `internetMapEnabled` 默认 False，但签名实际默认 True（`seedemu/compiler/Docker.py:495-535`）；真实基线应以签名为准，仍应在第一阶段显式传值以避免文档漂移。

## 10. 对第一阶段 Emulator 设计有直接影响的结论

1. **孤立 Satellite 是 SEED 当前 Docker Compiler 的新增正式能力，不是现成能力。** Node/Registry/Base 的早期阶段接受零接口，但 Docker 命名无条件访问第一个接口；不能靠命名模板、可视化设置或假链路解决。
2. **Registry 可扩展不等于系统可扩展。** 真正的扩展面包括 AS 生命周期、Base、options、Layer 扫描、Docker dispatch/role/naming 和可视化 metadata。
3. **`Node` 是当前最轻的可部署节点对象，但并不轻量。** Satellite 从它派生可复用编译合同；若重建一个更基础类型，实际上需要重定义大量 Compiler 合同。
4. **把 Satellite 注册为 `hnode` 是可行捷径但有明确语义债务。** 它会被标成 Host，并被 Routing 的单接口/默认网关规则捕获。
5. **GS 的地面路由能力不应仅由继承关系推断。** Router 只提供结构/API；forwarding option 来自 Base，daemon/loopback 来自 Routing，OSPF/iBGP/eBGP 来自各自 Layer。第一阶段必须明确选择哪些能力。
6. **现有 Router 组合可以裁剪协议参与，但不能裁剪成轻量 Node。** OSPF/iBGP mask 是真实扩展点，Routing 的 daemon/image/loopback 行为仍保留。
7. **运行时 InternetMap2 的图模型可以表达孤立节点。** 它按容器 labels 发现节点，vertex 不依赖 edge；不需要为了显示而创建假连接。当前真正阻塞在容器生成/启动之前。
8. **上传 compose 的可视化路径不支持零网络节点。** 第一阶段若要求实际启动后查看，应把运行时 `/pro/map` 路径作为验收对象，不应把上传式地图结果混为一谈。
9. **轨道、位置、稳定 ID 应先属于 Skyseed Emulator 的静态领域模型。** 需要进入 output/可视化的字段可投影为 namespaced SEED labels；`Node.setGeo()` 和 Registrable attributes 当前不会自动进入 Docker output。
10. **Simulator 只读 entity view 必须独立定义。** SEED Node/Registry/compose 都包含过多可变容器配置，不能直接充当 Emulator–Simulator 合同。
11. **下一步仍应是设计决策与最小验证计划，而不是迁移或实现。** 至少要先确认 Satellite/GS 的 registry 身份、AS 所有权、零接口 compiler 合同、GS 路由能力集合和可视化验收路径。
