# Skyseed v1 架构

## 产品边界

Skyseed 是库和实验平台，而不是固定的 S4 runner。它沿用 SEED Emulator 中逻辑描述与平台编译器分离的理念，并为随时间变化的卫星状态增加显式在线运行时。

```text
scenario/example
  -> SkyseedEmulator + selected layers/services
  -> logical topology IR
  -> planner IR
  -> backend compiler plan
  -> runtime scheduling and activation
  -> concrete backend execution
  -> observations and audits
```

Satellite Internet 1.0 S4 profile 是平台首个已验证示例，不是平台定义。

## 源码职责

- `core/`：稳定接口、IR、生命周期词汇、registry 原语和共享错误；禁止导入 BPF、Docker、Linux 主机、SEED 或场景代码。
- `layers/`：可选的整网能力，如星座、连通、路由、互联网接入、动态和观测。
- `services/`：安装到选定节点的流量、测量或控制服务。
- `models/`：可替换的轨道、可见性、zone、地面和链路策略。
- `topology/`：生成拓扑切片和稳定 `TopologySnapshot` IR。
- `planning/`：计算路由、G/Z FEC 意图、时间事件、参数和变更意图，不执行主机命令。
- `compilers/`：把语义 IR 纯翻译成后端计划；编译不得修改主机。
- `runtime/`：后端无关的事务顺序、horizon 准备、激活、退役、worker 协调和回滚编排。
- `backends/`：具体 BPF、Linux、qdisc、命名空间和 Compose 机制。
- `adapters/seedemu/`：Skyseed 逻辑对象与 SEED 对象、registry、binding、layer 和 compiler 的映射。
- `observability/`：记录原始事件、指标、计时、产物和运行状态。
- `audit/`：依据计划和观测判断拓扑、规划、运行时、后端和等价性声明。
- `operations/`：预检、主机就绪、清理和会话生命周期命令。
- `visualization/`：导出格式和展示专属资源。
- `cli/`：只负责参数解析和退出码；可选择并调用用例，但不能实现拓扑、规划、后端或审计逻辑。

## 稳定 IR 边界

首批稳定中间表示是 1.0 已有的兼容形态：`TopologySnapshot`、`RuntimeUpdatePlan`、`NetworkMutationPlan`、时间 `RuntimeEpoch`/`ParameterEpoch`、后端编译/准备计划，以及执行、回滚、观测和审计报告。

迁移可以移动或拆分实现，但不能静默改字段、削弱验证、改变序列化或指纹。Schema 变化属于单独语义变更，未经明确批准不属于纯结构重构。

## 依赖方向

依赖从策略和编排指向稳定抽象，core 永远不指向具体后端。

```text
core
  <- models
  <- topology
  <- planning
  <- compilers

core <- runtime -> backend interfaces
core <- backends
core <- adapters

observability <- runtime/backends
audit <- core IR + observations
cli/examples -> public interfaces only
```

附加规则：

1. planner 不调用 Docker、`tc`、`bpftool` 或命名空间命令；
2. compiler 产生数据和命令意图，但不应用；
3. runtime 决定何时改变状态，不决定 planner 为什么选择它；
4. backend 不选择路由、网关、zone 或 horizon 语义；
5. audit 不修复失败，也不绕过失败回退；
6. 实验选择属于 example，CLI 默认值不能隐藏第二套场景定义；
7. 切换后只有一条标准路线；
8. `experiment.py` 是唯一可编辑示例定义，effective profile 是写入运行目录的解析器输出。

## 从 1.0 保留的运行时不变量

- 网络激活是最高优先级路径；控制台、观测、审计投影和产物展示必须有界、延后、合并或允许降低新鲜度，不能反压 epoch 更新；
- 严格 `bpf_owned` 不存在 Linux G/Z、MPLS、LFIB、NAT 或 Mini-Internet 转发回退；
- EventTimeline 继续是运行时更新权威；
- 一次运行只有一条连续冻结物理链路时间轴；滚动 horizon 通过对齐帧偏移消费，不能各自重启物理链路选择；
- 每个准备好的 horizon 必须与初始 Compose 拓扑编译出的节点和 slot 物化身份完全一致；漂移在 Compose build 前失败，禁止运行时创建接口；
- 未来状态 inactive 准备，只在语义边界激活；
- 部分或不匹配的 worker 结果不是 ready；
- qdisc/链路生命周期继续由 Linux 拥有，除非未来门禁明确改变；
- 冻结重放输入保持固定，不匹配时失败关闭；
- 回滚和 postflight 必须留下零归属残留。

## 示例、测试和输出

- `examples/` 保存面向用户的可执行组合目录；每个示例拥有短 `run.py`、唯一 `experiment.py`、双语 README、忽略的 `output/` 和仅供该示例使用的夹具；
- `tests/` 按 unit、contract、integration、replay、system 分层；
- `data/` 保存可复用的已提交目录，不保存运行输出；
- 每个示例的忽略 `output/` 保存生成产物，清理后删除即可重置本地状态；
- 真实运行的长期结论进入 `docs/history/`，不进入源码或测试夹具。

## 目录增长规则

一个稳定概念若可能拥有实现、配置、夹具、产物说明、变体或 README，应获得带公开边界和归属声明的独立目录，而不是加入大型扁平文件列表；单个很小文件不为对称性强行嵌套。生成状态只能位于归属示例的忽略 `output/`，不能成为可编辑源码或隐式输入路径。

该规则适用于整个 v1，尤其是示例、后端家族、运行时维护、审计家族和工具；它是长期组织规则，不授权无契约/导入门禁的再次批量搬迁。当前拥挤清单和拆分候选见 `docs/architecture/directory_growth_ZH.md`。

## 暂缓设计方向

已讨论但尚未成为架构的想法保存在 `docs/architecture/design_backlog_ZH.md`。记录只为跨人类/AI 交接保存动机，不代表发布承诺、正确性不变量或昂贵运行门禁。
