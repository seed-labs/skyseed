# Satellite 目录导览

这个目录以 active 的 Skyseed operational-clean 卫星互联网平台为主线。
运行输出、日志、缓存等生成物不作为源码保留。

重构前已经验证的卫星互联网 1.0 基线，以及可重复执行的
prepare/replay/run/audit 流程，记录在
[`docs/skyseed_1_0_baseline.md`](docs/skyseed_1_0_baseline.md)。

## 主要区域

- `design_principle.md`
  - 当前 Iteration 2 的设计源文件
- `status.md`
  - 当前项目进展、里程碑和验证记录
- `FILE_MAP.md`
  - 当前目录的简明文件/目录职责说明
- `apps/`
  - 当前主线的正式可运行入口
- `docs/`
  - 当前架构说明和调查文档

## 当前主线

当前端到端主路径主要围绕这些文件：

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
- `apps/run_skyseed_validation_profile.py`
- `apps/run_starlink_shell_experiment.py`

## 当前 Operational-Clean 路线

当前推荐的 Skyseed 真实 runtime 路线是 `S4`
`rich_s4_operational_clean_bpf` validation profile。轨道数据来自
`apps/visualization/planned_shell_orbit_data_s1_s5.json`，基站数据来自
`apps/visualization/starlink_gateways.json`，并使用 rich G/Z UT/zone
catalog、event-time RuntimeEpoch / ParameterEpoch 队列、BPF-owned clean data
plane，以及 4 个 240 秒滚动 horizon。

正常入口顺序：

1. `apps/skyseed_session_check.py`：新会话交接和唯一 active validation
   profile command。
2. `apps/skyseed_preflight.py`：检查 stale runner、compose、direct-backend
   host artifact、runtime lock 和 host readiness。
3. `apps/prepare_host_mpls.py` 或 runner 的 `--host-prepare-policy auto`：
   VM reboot 后修复 MPLS/sysctl/bridge readiness。
4. `apps/run_skyseed_validation_profile.py`：真实 runtime 使用
   `--profile rich_s4_operational_clean_bpf`，除非目标明确要求
   preview-only。
5. `apps/inspect_temporal_run.py`、`apps/compare_temporal_runs.py`、
   `apps/summarize_starlink_long_run.py` 和
   `apps/generate_gz_6zone_2h_figures.py`：运行结束后的观测、汇总和图表包。

## 源树分类

- 稳定源码：根目录 runtime/planner/host 模块、`starlink_shell*.py`、
  `temporal_planning.py`、`ground_model.py`、`orbit_data.py`，以及
  `apps/run_starlink_shell_experiment.py` 等正式入口。
- 稳定文档/运行手册：`README*.md`、`status.md`、
  `docs/known_issues_runbook.md`、`docs/skyseed_node_role_boundary.md`、
  `apps/README.md`。
- 稳定测试：根目录 `test_*.py` 和流程级伴生检查
  `apps/pre_long_run_contract_check.py`。
- 生成物：`output/`、preview render 配置、TLE cache payload、
  `__pycache__/`。这些不作为源码提交。
- 探索/原型工具：24/32-zone catalog 生成、preview matrix、TLE fetch、
  goal/return-path/distance inspector。它们保留为证据和分析工具，不是
  canonical 启动命令。
- 临时/debug 残留：bytecode cache 和一次性 runtime log，继续忽略。
`output/`、`__pycache__/` 等目录都是生成物，不作为源码保留。
