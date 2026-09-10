# Skyseed Next

本仓库用于重新设计和实现卫星互联网仿真系统。

本项目不是对旧版本代码的整体重构或迁移，而是在重新完成系统设计的基础上，以可验证的小步迭代方式建立新的实现。

旧版本中已经验证过的算法、运行机制和性能优化不会被直接丢弃，但也不会被整体复制到新系统中。只有当当前设计涉及相关能力时，才对旧版本进行定向调查，并在新设计的边界内重新吸收其中有价值的经验。

---

## 1. 设计批准与实现边界

每个切片先明确目标、公共合同、架构边界、批准范围和排除范围。批准
结论必须进入任务 Prompt 或仓库中的正式设计记录，不能只存在于对话
记忆中。

在已批准的公共合同和架构边界内，私有函数、局部变量、内部算法、
测试辅助代码和不改变语义的机械整理属于实现自主权。公共概念、数据
所有权、跨层合同、顶层模块或正式执行路径的新增和改变必须先提出
架构变更提案并获得明确批准。

长期规则和提案格式见 [`AGENTS.md`](./AGENTS.md)。

---

## 2. 循环开发流程

项目按模块或垂直切片持续循环推进。

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

### 1. 调查事实

先调查当前代码、依赖、运行行为和已有证据。只有在当前能力与旧版本
已验证机制相关时，才进行范围受限的历史调查。调查默认只读，输出
事实、参数、算法、公式、规模、性能和失败经验，不直接迁移旧结构。

### 2. 明确设计、所有权和合同

根据事实明确对象职责、数据所有权、父子关系、依赖方向、输入输出、
正式执行路径和验收方式。随后在任务 Prompt 或正式设计记录中写清：

```text
本轮批准新增或修改：
- ...

本轮明确不包含：
- ...
```

公共合同冻结后才能进入实现。未批准的新公共概念必须停留在提案状态。

### 3. 实现与自动测试

在冻结边界内实现代码、测试和必要的运行验证。测试通过是必要条件，
但不能单独代表切片完成。

### 4. 架构增量与人类审核

完成独立的 `Architecture Delta`，检查公共类型、方法、属性、所有权、
依赖、目录、export、跨层合同、执行路径、类图和 example 调用链。
同时提供最小 example 或等价调用片段，使真实职责可以通过少量跳转
到达。

### 5. 同步、接受与冻结

同步代码、测试、公共 API 清单、类图、流程图、术语、example README、
CURRENT 和验证记录。只有全部接受条件满足且工作区 clean，当前切片
才能冻结并成为下一切片的输入。

---

## 3. 旧版本的定位

旧版本是：

* 工程经验来源；
* 已验证机制的参考；
* 性能基线；
* 历史失败案例来源；
* 必要时的行为对照。

旧版本不是：

* 新系统的基础架构；
* 等待整体迁移的代码库；
* 新系统运行时的 fallback；
* 新系统模块和对象划分的模板；
* 新实现成功的替代证明。

新系统不得依赖旧版本才能完成正式运行。

---

## 4. 基本约束

### 新设计优先

任何旧版本机制都必须适应新设计，而不是要求新设计迁就旧代码。

### 调查与实现分开

旧版本调查应先形成结论，再决定是否以及如何实现。不得在调查过程中顺便搬迁代码。

### 小步闭环

每一轮应完成一个可理解、可测试、可审查的切片，避免一次性实现过大的系统范围。

### 代码、测试和文档同步

实现完成时，相关测试和文档也应处于一致状态。

测试通过不能替代真实运行验证，真实运行成功也不能替代合同和单元测试。

### 不允许隐式绕行

不得为了临时跑通而加入未经设计批准的 fallback、兼容路径、特殊条件或第二条成功路径。

### 设计变更必须显式

实现过程中发现设计问题时，应明确提出设计变更，而不是通过代码细节悄悄改变系统行为。

### 冻结能力变更必须单独批准

Simulator 正式拥有语义 Planner；BPF Compiler 与 `BpfRunner` 的所有权和
边界已获批准。权威架构见 [`docs/accepted-design.md`](docs/accepted-design.md)，
当前状态见 [`docs/CURRENT.md`](docs/CURRENT.md)。改变已冻结的所有权/公共合同，
或引入新 backend、dataplane、公共 plugin、lifecycle authority，必须先获得
明确设计批准。`ComposeProjectLauncher` 是可选 deployment utility，不拥有
`BpfRunner` lifecycle。
v1 只能围绕当前切片做定向事实调查，不能整体迁移。测试 PASS 也不能
替代真实执行证据。

### 接受不等于测试通过

切片接受还要求 Architecture Delta 已审核、最小 example 可理解、公共
类型和数据所有权已确认、类图与术语同步、未经批准的概念已删除或暂停，
并且工作区 clean。后续改变冻结合同必须作为新的显式架构变更提出。

---

## 5. 正式事实来源

项目事实与授权按以下分工管理：

* `AGENTS.md`：长期工程与架构治理规则；
* 每轮任务 Prompt：该切片批准范围和明确排除范围；
* `docs/CURRENT.md`：当前阶段、已接受状态和下一目标；
* 设计与架构文档：已冻结合同、关系和依赖方向；
* 源代码与测试：当前实现及自动合同；
* example 与验证记录：人类审核入口和可复现证据。

聊天记录与记忆只能协助工作，不能替代这些正式记录。

---

## 6. 状态与架构入口

本 README 不重复维护当前阶段、下一目标或当前包结构：

* 文档身份与入口见 [`docs/README.md`](./docs/README.md)；
* 当前阶段与下一目标见 [`docs/CURRENT.md`](./docs/CURRENT.md)；
* 最高架构锚点见 [`docs/accepted-design.md`](./docs/accepted-design.md)；
* 人工架构图见 [`docs/human-readable/`](./docs/human-readable/)；
* 公共合同和维护映射见
  [`docs/codex-reference/`](./docs/codex-reference/)；
* 操作入口见 [`docs/user-guides/`](./docs/user-guides/)。

## 7. 本文件的使用

本文件规定项目的基本设计与开发流程。

长期工程约束集中在唯一的根级 [`AGENTS.md`](./AGENTS.md)。本 README
解释开发方法，但不建立第二套规则。流程需要调整时，应作为明确的
文档变更进行审核，并同步两个文件中受影响的说明。

## 8. 本地开发环境

canonical local development environment 是 Conda 环境 `skyseed_dev`。
首次配置时，从本仓库根目录运行：

```bash
scripts/setup_dev_environment.sh
conda activate skyseed_dev
python scripts/check_dev_environment.py
```

Python 项目依赖仍由 `pyproject.toml` 管理；`environment.yml` 只固定本地
环境名称、Python 版本和 pip。普通 venv 和 CI 只要安装相同项目依赖仍然
受支持，examples 不会自动切换解释器或寻找 `.venv`。

当前 package index 不发布项目固定的 `seedemu==0.0.7`。setup 脚本默认
使用与 `skyseed/` 同级的 `seed-emulator/` checkout；其他布局可显式设置
`SEED_EMULATOR_CHECKOUT`。该处理不在 environment.yml 中重复维护 Python
依赖版本。

首次在 VS Code 打开 workspace 时，执行一次 **Python: Select
Interpreter** 并选择 `skyseed_dev`。仓库设置会在后续新终端中激活已选择
的解释器，但不能替代首次选择。

BPF Runner 在初始 Preparation 中完成首个密封交付的 finite plan materialization、
capacity/identity validation 与 generation-semantic BPF preload，并实际激活 boundary 0。
单交付模式在此准备整个有限序列；连续交付由同一个 Runner/backend 的
Maintenance 在 Main 运行期间加载、构造和预写后继批次，并在实际激活与
reader/reference 安全条件满足后回收旧数据。容量固定 admission，deadline
前未 READY 则明确失败，不移动时钟或跳过边界。
`run()` 从 boundary 1 开始，使用长期 privileged channel、retained namespace
sockets 和 direct netlink/BPF syscalls 提交当前 delta。Maintenance 使用有界
请求并让出 Main channel；不会推迟当轮正式撤销。`SKYSEED_BPF_RUNTIME_WORKERS` 限制 Preparation
中独立 startup namespace audit/scaffolding 工作，以及 Main/Cleanup 的独立
namespace 删除线程（1–64，默认不超过 CPU 数与 8）。删除线程在 Preparation
建立并复用；每轮 Main 等待当轮物理删除全部完成。
Main 只记录 compact boundary/barrier timing；bulk telemetry identity 写入由后台处理。
连续交付配置和调用入口见
[S1–S5 Example](examples/starlink_multi_shell_satellite_internet/README.md)。

## 9. Offline Tooling Baseline

从 `examples/complete_static_emulator/` 执行默认入口：

```bash
python main.py
```

同一个 Emulator 会创建一次 Simulator/PhysicalWorld，并在 Compose 编译完成后
生成 `examples/complete_static_emulator/output/runtime/docker-compose.yml`、
start/cleanup controls，以及同一示例自有 `output/inspection/index.html`。
默认 output 由 `main.py` 所在目录锚定，不受 shell 当前目录影响；
`--output PATH` 可显式覆盖。inspection 包括联动 3D/2D Physical World、Zone、
分阶段 Planner、紧凑 Compiler 和 System Observation 页面；精确 PhysicalFrame
事实由 Physical World Tool 在同一时间游标下检查，不另设独立 Tool。
页面内嵌或相邻保存本次离线数据；这些 JSON 是私有 tooling audit artifact，
不是 Simulator 或领域合同。独立的 `simu.py` 保留可读的正式调用链，不
生成工具。
