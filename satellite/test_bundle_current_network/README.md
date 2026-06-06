# Current Satellite Network Test Bundle

这套测试包面向当前 `satellite/` 的 Iteration 2 主线，目标是把“现在能稳定自动化的测试”整理成一个新的独立目录，方便你直接执行、留档和检阅。

## 这套测试分三层

1. `host preflight`
   检查 Python、`seedemu` 依赖、Docker、`docker-compose`、配置文件，以及当前容器是否已经运行。`output/` 和 compose 文件是生成物，缺失时只提示如何生成，不会把干净仓库状态判为失败。

2. `offline validation`
   不改动容器网络，直接调用现有的 `satellite/apps/iteration2_satellite_experiment.py` 做一次 iteration2 的离线回归验证，重点看：
   - contract / planner / mutation validation 是否全部通过
   - slice 数量和切换摘要是否合理
   - `ut1 -> as160-host1` 这条关键观测流在不同 slice 下的路径是否符合预期

3. `real network checks`
   只在容器已经启动时继续执行，不会自动 `docker-compose up -d`。主要做：
   - `ut1` 对 `as160-host1` 和 `as154-host-new` 的短 ping
   - `sat-a1` / `sat-a2` / `gw1` / `gw2` 的接口与 LFIB 观察

## 目录结构

```text
satellite/test_bundle_current_network/
├── README.md
├── scripts/
│   ├── run_all_tests.sh
│   ├── run_full_runtime_validation.sh
│   ├── run_host_preflight.sh
│   ├── run_offline_validation.py
│   └── run_real_network_checks.sh
└── logs/
```

## 推荐执行方式

在仓库根目录 `/home/seed/skyseed` 下执行：

```bash
bash satellite/test_bundle_current_network/scripts/run_all_tests.sh
```

执行后会在 `satellite/test_bundle_current_network/logs/<timestamp>/` 下生成：

- `host_preflight.log`
- `offline_validation_summary.json`
- `offline_validation.stdout.log`
- `real_network_checks.log`
- `run_manifest.txt`

## 对当前项目的测试建议

- 每次改 `planner.py`、`experiment_topology.py`、`network_mutation.py`、`network_runtime.py` 之后，先跑一遍 `offline validation`。
- 每次改 `linux_mpls_runtime.py`、`controller.py`、MPLS/LFIB 安装逻辑之后，再补跑 `real network checks`。
- 如果你要观测真实切片切换，建议把这里的测试包和现有文档一起用：
  - `satellite/docs/guides/iteration2_mpls_observation_guide.md`

## 完整真实验收

如果你想把当前 Iteration 2 小规模网络完整跑起来，并验证 slice 切换期间的数据面状态，执行：

```bash
bash satellite/test_bundle_current_network/scripts/run_full_runtime_validation.sh
```

这个脚本会：

- 先清理旧的 `iteration2_small_scale` / `iteration2_small_scale_dynamic`
  compose 容器、网络和 volume 残留
- 非交互式加载宿主机 MPLS 模块：
  `mpls_router`、`mpls_iptunnel`、`mpls_gso`
- 设置宿主机 `net.mpls.platform_labels=1048575`
- 重新编译当前 iteration2 small-scale 输出
- `docker-compose up -d`
- 记录 `ut1` 在 runtime 安装前的“不可达”基线
- 启动 `ut1 -> as160-host1` 和 `ut1 -> as154-host-new` 的后台 ping watcher
- 运行 `iteration2_satellite_experiment.py --execute-real --continuous-real`
- 自动收集 slice 切换、连通性、路径观测、watcher 丢包/恢复统计
- 默认在结束后 `docker-compose down`，把环境收回到干净状态

## 说明

- `run_all_tests.sh` 默认不会主动启动或停止容器，避免打扰你当前环境。
- 如果 `real network checks` 发现容器未运行，会在日志里明确写成 `SKIPPED`，这不算脚本错误。
- `run_full_runtime_validation.sh` 会主动清理并重建当前实验环境；它依赖当前用户可以执行
  `sudo -n modprobe ...` 和 `sudo -n sysctl ...`，如果宿主机没有 MPLS 内核能力或 sudo
  无法非交互执行，它会明确报出环境 blocker。
