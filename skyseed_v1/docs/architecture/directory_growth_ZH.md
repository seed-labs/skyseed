# 目录增长审计

本审计落实 v1 规则：一个概念若可能拥有实现、配置、夹具、变体或文档，应形成带明确公开边界的独立目录。本文本身不授权再次批量搬迁。

## 当前结果

示例、测试、后端家族、数据、文档和原生源码边界均以目录组织。`examples/s4_operational_clean/`、`examples/s5_operational_clean/` 使用参考布局：`run.py`、唯一可编辑的 `experiment.py`、README 和被忽略的本地 `output/`。每个顶层源码职责都有本地 README，廉价测试树有直接 `tests/run.py` 入口和分层目录。

剩余拥挤风险集中在少数大型编排模块，而不是宽而扁平的目录：

| 当前归属 | 约略大小 | 最终包边界 |
|---|---:|---|
| `runtime/experiment.py` | 1.55 MB | 生命周期 runner、计时、进度、产物、清理、验收投影 |
| `backends/linux/mpls.py` | 651 KB | 规划/spec、应用、验证、会话、BPF-owned bridge 契约 |
| `backends/linux/network.py` | 252 KB | 清单、slot 生命周期、qdisc、回滚 |
| `runtime/profile.py` | 245 KB | profile 启动、清理和派生验收审计 |
| `backends/bpf/live.py` | 162 KB | map、挂载生命周期、计数器、清理 |
| `planning/temporal.py` | 147 KB | 事件模型、horizon 规划、序列化 |

这些是未来 module-to-package 拆分候选。每次拆分必须先用聚焦测试锁定公开导入、序列化产物、失败标签和成功调用路线。一次移动全部模块会重新制造结构切换要控制的风险。

## 未来拆分的必要形态

- 包 `README_EN.md`/`README_ZH.md`：职责、公开导入、禁止依赖；
- `__init__.py`：刻意保持很小的公开表面；
- 契约/模型代码与执行代码分离；
- 夹具和预期证据放在归属测试/示例目录，而非生产源码；
- 生成产物进入示例自己的忽略 `output/`，自动化也可显式选择外部 run root；
- 一个概念一个目录，不为了对称而制造单文件嵌套。

优先级依据实际变更压力和 bug 密度。首个候选是 `runtime/experiment.py`，但只能小步抽取（如控制台/进度、计时推导、产物投影），每一步通过等价测试后再继续。
