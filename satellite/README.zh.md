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
