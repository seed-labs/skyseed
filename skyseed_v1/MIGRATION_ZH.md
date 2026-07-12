# Satellite Internet 1.0 迁移

## 管理基线

- Skyseed tag：`satellite-internet-v1.0-baseline`
- Skyseed commit：`fbb7ff849606b95d2c7ab4399be35656153d5b1c`
- SEED 依赖仓库：`/home/seed/seed-emulator-upstream-dev`
- SEED 分支：`master`
- 迁移开始时的 SEED commit：`61acdf4c9699b25b06843ec4aad747108b05924e`
- Python：`/home/seed/miniconda3/envs/seed310/bin/python`（3.10.20）

准确成功路线和验收事实记录在 `docs/migration/baseline_ZH.md`。

## 迁移规则

把仍需要的实现搬入新的归属边界；不要在旧树中逐文件删除，也不要保留第二套活跃实现作为兼容回退。构建期间 `satellite/` 是基线执行器，`skyseed_v1/` 尚非标准实现；切换时新路线成为标准，被取代的活跃代码从当前表面移除。

## 初始归属清单

下表按职责描述；每个迁移切片开始前会展开到准确符号和测试。

| 当前来源 | 目标归属 | 迁移门禁 |
|---|---|---|
| `contracts.py` | `core/ir/` | 序列化、指纹、验证结果 |
| `topology_pipeline.py` | `core/interfaces/`、`models/`、`topology/` | 拓扑契约单元测试 |
| `orbit_data.py`、`ground_model.py` | `models/orbit/`、`models/ground/` | 目录和坐标等价 |
| `starlink_shell*.py`、`experiment_topology.py` | `topology/`、`models/` | 快照和预览等价 |
| `planner.py`、`planner_validation.py` | `planning/routing/` | 规划意图和失败测试 |
| `temporal_planning.py` | `planning/temporal.py`、`planning/gz_fec/` | 有序事件和 FEC diff 测试 |
| `network_mutation.py`、`runtime_projection.py` | `planning/mutations.py`、`compilers/` | 计划投影等价 |
| 分发、agent、installer 文件 | `runtime/`，之后为 `services/` | 交付/安装契约测试 |
| `horizon_maintenance*.py` | `runtime/horizons.py`、`maintenance.py`、`worker.py` | worker 交接和生命周期重放 |
| `linux_mpls_runtime.py` | 规划、编译、运行时和 Linux/BPF 后端 | 纵向切片测试；禁止整文件搬迁 |
| `network_runtime.py`、`mpls_host.py` | `backends/linux/`、`operations/` | 主机生命周期和回滚测试 |
| `bpf_mpls_live.py`、活跃 BPF C 源码 | `backends/bpf/` | 离线编译、loader、map ABI 测试 |
| `seed_integration.py` | `adapters/seedemu/` | SEED master 适配器测试 |
| 运行 trace/指标/检查器 | `observability/` | 产物和计时契约测试 |
| 返回路径、队列、回归审计 | `audit/` | 审计真值表测试 |
| 预检/会话/清理应用 | `operations/`、轻量 `cli/` | 环境检测测试 |
| 验证 profile 和目录 | S4 示例、`data/` | 生成命令和选择等价 |
| 受支持应用入口 | 轻量 `cli/` 加公开用例 | CLI 不含领域/运行时实现 |
| 预览/分析/基准应用 | `tools/` 或示例帮助器 | 核心运行时不导入 |
| 根 `test_*.py` | 与归属匹配的 `tests/` 层级 | 断言稳定行为，不断言旧路径 |
| 实验轮次文档 | `docs/history/` 来源记录 | 永不导入或执行 |
| 被忽略输出/缓存/TLE | 继续生成并忽略 | 不跟踪运行残留 |

## 阶段

0. 冻结基线、清单、验证矩阵和成功路线证据。
1. 建立可安装包骨架和架构规则。
2. 搬迁核心 IR、接口和无副作用模块及测试。
3. 搬迁拓扑/规划，建立 S4 示例并发布验收报告 1。
4. 按纵向执行切片拆分编译器、运行时和后端。
5. 落地纯 CLI、切换标准示例并发布验收报告 2。
6. 整理审计、可观测性、运维、工具、数据和测试层级。
7. 通过离线门禁，仅执行必需的系统/长跑验证，完成切换并发布最终报告。

Stage 7 离线门禁变绿前禁止长跑。
