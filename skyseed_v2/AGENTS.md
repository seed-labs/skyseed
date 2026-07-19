# Skyseed v2 Engineering and Architecture Rules

本文件只保存长期适用的工程与架构治理规则。开始工作前阅读
`README.md`、`docs/CURRENT.md` 和 `docs/architecture/terminology.md`。

当前阶段和下一目标只记录在 `docs/CURRENT.md`；每轮批准范围和排除
范围以该轮任务 Prompt 为准。不得把当前提交、分支、阶段状态、临时
候选设计或具体收尾任务写入本文件。

## 1. 标准纵向开发流程

每个功能切片按以下顺序推进：

```text
调查事实
→ 领域设计
→ 公共合同冻结
→ Codex 实现
→ 自动测试
→ 架构增量审计
→ 最小 example 人工审核
→ 类图 / 术语 / CURRENT 同步
→ 接受切片
→ 下一切片
```

测试通过不能单独代表切片完成。调查、合同冻结、架构审计、人工可读
入口、真实执行证据和文档同步都是接受条件。

Simulator conceptual diagrams 用于保存长期职责边界和待细化的设计空间，
不是实现任务清单或公共 API 冻结清单。不得创建大量空类来匹配图；每个
纵向切片只冻结本轮真正需要的公共概念。后续切片可以根据获批设计和真实
实现修改、拆分、合并或重命名 conceptual 对象，不能只因尚未实现就从图中
删除它们。

Planner、限定职责的 Compiler 和 conceptual `SimulationRunner` 必须保持一条
明确的批准路径，不得形成隐式重规划、后端 fallback 或其他成功路径。测试
PASS 不替代真实执行证据。

## 2. 已批准范围内的实现自主权

在本轮已经批准的公共合同和架构边界内，可以自行决定：

- 私有函数和私有 helper；
- 局部变量和内部算法；
- 测试 fixture 与测试辅助代码；
- 不改变语义的机械整理；
- 已批准接口内部的实现细节。

这些实现细节默认不进入根公共 API、跨层合同、领域类图或项目术语
表。实现便利不能自然升级为公共或领域概念。

文件输入、Config 类型、schema、公共创建入口、模块所有权和跨层数据合同
属于公共架构。此类任务必须先经明确设计批准；实现代理只能在批准合同
内部自由决定私有解析、辅助函数和测试实现，不得根据“支持文件读取”自行
创造新的公共层、聚合根、结果包装或文件格式。

## 3. 公共架构变更必须先申请

新增或改变下列内容且尚未包含在本轮批准范围内时，必须暂停有争议
部分、提出设计问题并等待明确批准：

- 公共类、公共函数或公共枚举；
- 实体的重要属性语义；
- 数据所有权；
- 父子关系或包含关系；
- 跨层输入和输出；
- 根包 export；
- 顶层模块或目录边界；
- 正式执行路径；
- 新运行模式或 fallback；
- 类图中的主体类或重要关系；
- Emulator、Simulator、Topology、SemanticPlanner、BackendPlanner、
  `SkyseedDockerCompiler`、conceptual `ExecutionCompiler`、
  `SimulationRunner`、Observation、Materialization 之间的职责。

禁止先提交实现，再在完成报告中解释新概念。与争议无关且已经批准
的工作可以继续，但争议部分必须停留在提案状态。

## 4. 架构变更提案格式

发现需要新的公共概念时，先提交以下提案：

```text
候选名称：

它是什么：

它不是什么：

所属层：

谁创建：

谁拥有：

谁使用：

长期消费者：

现有类型为什么不能承担：

不增加它的替代方案：

公共 API 影响：

依赖方向影响：

类图和 example 影响：

建议纳入当前切片还是后续切片：
```

未获批准前，不得将候选概念加入正式代码、公共 API、术语表或当前
架构图。

## 5. 使用批准范围，不使用机械数量限制

不规定“每轮最多新增几个类或属性”。每轮必须明确：

```text
本轮批准新增或修改：
- ...

本轮明确不包含：
- ...
```

批准类型内部符合既定语义的普通字段可以直接实现。若字段引入新的
所有权、生命周期、依赖、运行资源或跨层知识，则属于公共架构变化，
必须先申请。

## 6. Architecture Delta

每轮完成报告必须包含独立的 `Architecture Delta` 章节，至少检查：

- 新增、删除、重命名的公共类型；
- 新增或修改的公共方法；
- 重要属性变化；
- 数据所有权变化；
- 父子关系变化；
- 模块依赖变化；
- 根 export 变化；
- 顶层目录变化；
- 跨层合同变化；
- 正式执行路径变化；
- 类图变化；
- example 调用链变化。

如果没有变化，明确写：

```text
Architecture Delta: none
```

## 7. Example 是人类审核入口

每个切片应提供最小 example、调用片段或等价入口，使读者能够回答：

```text
输入是什么？
调用哪个方法？
返回什么？
谁拥有返回对象？
下一步传给谁？
```

关键调用应通过少量跳转到达真实实现。不得使用多层 facade、Builder、
Manager、Context、Coordinator 或 Factory 链掩盖真实职责。

## 8. 命名与概念治理

公共名称应尽量一词一义。避免没有完整限定语义的泛词：

```text
Scenario
Catalog
Builder
Population
Result
Context
Manager
Coordinator
State
Service
```

如确需使用，名称和文档必须明确其唯一含义和所属层。每个公共类型的
说明至少回答：它是什么、它不是什么、谁创建、谁使用。

私有 helper 和操作结果默认保持内部，除非存在明确、长期的公共消费
者。不得为了目录分类制造空壳层，也不得用 facade 隐藏不清晰的调用
链。

跨层稳定身份必须是无查找能力的纯值，并且只有一套权威派生规则。
实体和资源查找必须由真实所有者提供职责狭窄的方法；不得建立跨越实体、
编译结果和运行资源的全局索引，也不得由下游通过显示名称、容器名称、
服务名称或其他字符串重新猜测。缺失、重复、类型不符或所有者不匹配时
必须 fail-closed。

架构记录和公共合同必须区分静态部署链与动态执行链：

- `SkyseedDockerCompiler` 将 Emulator/SEED 静态模型降低为 Docker、
  Compose、service 和 network 等静态部署配置；
- 动态链的 approved direction 先区分后端中立的网络语义规划与
  后端特定的执行规划，再由 conceptual `ExecutionCompiler` 降低已经
  批准的后端执行计划，交给 conceptual `SimulationRunner` 执行。

`SkyseedDockerCompiler` 与 conceptual `ExecutionCompiler` 不得合并、互相
继承、共享无真实需求的模糊公共父类，或互相承担对方职责。旧的
`abstract Simulation Plan → SimulationPlanCompiler → concrete dynamic
operations` 是 retired/corrected 的过度压缩描述：它混合了网络语义规划、
后端实现规划和执行形式编译，不再是已批准的唯一主合同。所有动态类型和
最终公开名称仍须作为公共架构变更逐片审批。文档在可能同时涉及两条链时
不得使用无完整限定语义的单独 `Compiler` 或 `Runner`，也不得仅为名称
对称创建共同父类。当前动态执行者的 conceptual 限定名是
`SimulationRunner`；`ComposeProjectLauncher` 不承担该职责。

Simulator 静态输入类型是接收侧定义的跨层边界数据，只能依赖中性纯值和
自身数据类型；它们不是 Simulator 内部长久使用的完整领域模型。了解
Emulator 实体结构的输入生成适配代码必须位于 Simulator 核心之外；
Simulator 接收已生成的纯输入，不反向读取 Emulator、SEED Node 或
Registry。该正式边界类型名是 `SimulatorStaticInput`，生成入口是
`create_simulator_static_input(...)`，`SimulationDefinition` 通过
`static_input` 字段接收它；不得为此前审计中的错误名称保留兼容 alias。
example 的共享 helper 只构造 upstream Emulator，Emulator 运行入口和
Emulator→Simulator 外部装配入口必须保持分离。

## 9. 文档与实现同步

公共类、方法、属性、所有权或模块边界变化时，必须按影响同步更新：

- Mermaid 类图；
- 调用流程图；
- 模块依赖图；
- 公共 API 清单；
- 术语表；
- example README；
- 验证记录；
- `docs/CURRENT.md` 中受影响的阶段描述。

设计阶段临时命名与正式实现不一致时，记录：

```text
设计临时名
→ 当前正式名
→ 改名原因
→ 职责是否保持
```

类图用于发现架构漂移，不只是展示结果。

Conceptual class diagram 必须明确区分 implemented、approved direction、
conceptual candidate、open question 和 retired/corrected。它可以包含尚未
实现的候选对象以保存设计空间，但不得因此把候选对象写入 CURRENT 的
已实现列表、根导出或正式运行路径。

## 10. 历史实现调查边界

调查旧实现时，只提取：

- 事实、参数、算法和公式；
- 时间基准和实验规模；
- 已验证行为；
- 性能与失败经验。

不得直接迁移：

- 类名或目录结构；
- helper 调用链；
- 内部 dict 或文件格式；
- 历史控制流；
- fallback；
- 特定实验 hack。

调查结果必须先转化为当前架构的设计与合同，再进入实现。

## 11. 切片接受条件

一个切片只有同时满足以下条件，才可作为下一切片的冻结输入：

- 功能测试通过；
- Architecture Delta 已审核；
- 最小 example 可理解；
- 公共类型和数据所有权已确认；
- 类图、术语和相关文档同步；
- 未经批准的概念已删除、内部化或暂停；
- 工作区 clean。

后续若需要改变已冻结合同，必须作为新的显式架构变更提出，不能顺带
修改。
