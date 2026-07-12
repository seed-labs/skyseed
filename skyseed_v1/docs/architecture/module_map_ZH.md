# 模块归属图

本图描述切换完成后的标准事实来源。v1 项目完全独立：任何模块都不得从迁移基线树导入或解析实现、输入、缓存、锁、预览生产器或输出目录。

| 职责 | 归属 |
|---|---|
| 稳定 IR 与接口 | `core/ir`、`core/interfaces` |
| 轨道和地面输入模型 | `models` |
| 逻辑拓扑构建 | `topology` |
| 路由、时间和变更意图 | `planning` |
| 纯后端计划翻译 | `compilers` |
| 事务和实验生命周期 | `runtime` |
| Linux 与 BPF 机制 | `backends` |
| SEED 对象和层映射 | `adapters/seedemu` |
| 原始执行证据和指标 | `observability` |
| 通过/失败与等价性判断 | `audit` |
| 主机就绪和会话生命周期 | `operations` |
| 可视化交换与物理链路导出 | `visualization` |
| 参数解析和退出码 | `cli` |

标准 S4、S5 组合位于 `examples/`，可复用的轨道和 shell 专属地面目录位于 `data/`；两者都不定义平台核心行为。可视化交换契约位于 `docs/interfaces/visualization/`。

## 导入规则

- 跨归属边界使用绝对 `skyseed_v1.*` 导入；
- 包模块不得导入或解析已退役的迁移实现；
- 审计可以读取 IR 和观测，但不能修复运行时状态；
- 可观测性记录事实，不决定验收；
- 外部 SEED 导入隔离在适配器之后，只有必须实例化 SEED 原语的拓扑构建器例外；
- 生产模块永远不能导入工具或测试。
