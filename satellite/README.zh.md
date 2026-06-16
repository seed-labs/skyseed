# Satellite 目录导览

这个目录现在以 active 的 Iteration 2 卫星互联网研究平台为主线。
较老的 Phase1 / controller-driven 材料已经隔离到 `legacy_phase1/`；
运行输出、日志、缓存等生成物不作为源码保留。

## 主要区域

- `design_principle.md`
  - 当前 Iteration 2 的设计源文件
- `status.md`
  - 当前项目进展、里程碑和验证记录
- `FILE_MAP.md`
  - 当前目录的简明文件/目录职责说明
- `apps/`
  - 当前 Iteration 2 主线的正式可运行入口
- `legacy_phase1/`
  - 隔离保存的老 Phase1 脚本、文档和 baseline 配置
- `docs/`
  - 当前 Iteration 2 架构说明和调查文档
- `test_bundle_current_network/`
  - 当前自动化测试脚本，包括 host preflight、离线验证和完整真实 runtime 验收

## 当前 Iteration 2 主线

当前端到端 Iteration 2 主路径主要围绕这些文件：

- `experiment_topology.py`
- `planner.py`
- `contracts.py`
- `runtime_projection.py`
- `agent_envelope.py`
- `distribution_store.py`
- `node_local_agent.py`
- `installer_runtime.py`
- `network_mutation.py`
- `network_runtime.py`
- `linux_mpls_runtime.py`
- `seed_integration.py`
- `apps/build_iteration2_example_topology.py`
- `apps/iteration2_satellite_experiment.py`

## 当前稳定 Full-Shell 路线

当前推荐的 Skyseed 真实 runtime 路线是 planned-shell `S5` full `1-shell`
路径：`172` 颗卫星、6-zone metadata catalog、12UT/6GW valid6 ground
catalog、`--ground-placement catalog`、event-time RuntimeEpoch /
ParameterEpoch 队列、实时 replenishment、G/Z FEC diff planner，以及
SEED `dynamic_satellite_link=0` 的 direct backend。

正常入口顺序：

1. `apps/skyseed_session_check.py`：新会话交接和 canonical command profile。
2. `apps/skyseed_preflight.py`：检查 stale runner、compose、direct-backend
   host artifact、runtime lock 和 host readiness。
3. `apps/prepare_host_mpls.py` 或 runner 的 `--host-prepare-policy auto`：
   VM reboot 后修复 MPLS/sysctl/bridge readiness。
4. `apps/run_starlink_shell_experiment.py`：真实 runtime 只使用当前稳定的
   6-zone metadata catalog，除非目标明确要求 preview-only。
5. `apps/inspect_temporal_run.py`、`apps/compare_temporal_runs.py`、
   `apps/summarize_starlink_long_run.py` 和
   `apps/generate_gz_6zone_2h_figures.py`：运行结束后的观测、汇总和图表包。

24-zone 和 32-zone catalog 现在是 preview-only 探索材料，不能作为
canonical real-runtime 路径。原因和证据见
`docs/known_issues_runbook.md`。

## 源树分类

- 稳定源码：根目录 runtime/planner/host 模块、`starlink_shell*.py`、
  `temporal_planning.py`、`ground_model.py`、`orbit_data.py`，以及
  `apps/run_starlink_shell_experiment.py` 等正式入口。
- 稳定文档/运行手册：`README*.md`、`status.md`、
  `docs/known_issues_runbook.md`、`docs/skyseed_node_role_boundary.md`、
  `apps/README.md`。
- 稳定测试：根目录 `test_*.py` 和 `test_bundle_current_network/scripts/`。
- 生成物：`output/`、`legacy_phase1/output/`、
  `test_bundle_current_network/logs/`、preview/runtime-attempt render 配置、
  TLE cache payload、`__pycache__/`。这些不作为源码提交。
- 探索/原型工具：24/32-zone catalog 生成、preview matrix、TLE fetch、
  goal/return-path/distance inspector。它们保留为证据和分析工具，不是
  canonical 启动命令。
- 临时/debug 残留：bytecode cache 和一次性 runtime log，继续忽略。
- 过时/偏离尝试：`legacy_phase1/` 和旧 runtime-attempt 路径作为历史参考
  或兼容材料保留，不代表当前稳定路线。

## 共享 / 过渡文件

有些根目录文件虽然比较老，但 Iteration 2 目前还在复用，所以暂时保留：

- `build_topology.py`
  - 旧版 broad SEED 拓扑构建器，当前作为兼容/参考保留
- `controller.py`
  - 当前 host-side mutation 仍然会通过它的 public boundary 落地
- `mpls_control.py`
  - 仍作为 legacy MPLS/control 参考和少量过渡 helper 来源
- `legacy_contract_adapters.py`
  - 旧 controller/link-state 到当前 contract 的投影 helper，已从核心
    contract schema 隔离出来
- `runtime_update_agent.sh`
  - 为 controller 兼容性保留的旧 runtime-agent 辅助脚本

这些文件不是长期 Iteration 2 目标架构，但现在也还不能简单删除。

## 配置和输出

- `configs/iteration2_small_scale/`
  - 干净的 Iteration 2 topology inventory 与 link-state baseline
- `output/iteration2_small_scale/`
  - 由 `apps/build_iteration2_example_topology.py` 按需生成
- `legacy_phase1/configs/`
  - 旧的 broad baseline inventory 和 link-state

`output/`、`legacy_phase1/output/`、`test_bundle_current_network/logs/`、
`__pycache__/` 等目录都是生成物，不作为源码保留。

## 如果你要看什么

- 看老版主线：`legacy_phase1/README.md`
- 看当前 Iteration 2 demo：`apps/iteration2_satellite_experiment.py`
- 看当前观测方法：`docs/guides/iteration2_mpls_observation_guide.md`
- 看底层网络拆分候选：`docs/network_lower_layer_inventory.md`
- 跑完整干净环境验收：`test_bundle_current_network/scripts/run_full_runtime_validation.sh`
