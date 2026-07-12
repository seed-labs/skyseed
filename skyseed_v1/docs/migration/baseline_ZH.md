# Satellite Internet 1.0 结构基线

记录于结构迁移开始时。

## 来源身份

| 项目 | 值 |
|---|---|
| Skyseed tag | `satellite-internet-v1.0-baseline` |
| Skyseed commit | `fbb7ff849606b95d2c7ab4399be35656153d5b1c` |
| SEED 分支 | `master` |
| SEED commit | `61acdf4c9699b25b06843ec4aad747108b05924e` |
| Python | `seed310`，Python 3.10.20 |
| 标准 profile | `rich_s4_operational_clean_bpf` |

Shell 可能含有指向过时同级镜像的 `PYTHONPATH=/home/seed/seed-emulator`。受支持命令必须解析 `/home/seed/seed-emulator-upstream-dev` 的 editable 依赖，因此依赖身份属于每个门禁，而不是操作者假设。

## 成功路线

```text
session check / preflight / host prepare
  -> run_skyseed_validation_profile
  -> profile and catalog contract
  -> run_starlink_shell_experiment
  -> prepared frozen bundle
  -> ordered prepared consumer replay
  -> Compose and host materialization
  -> EventTimeline runtime queue
  -> H0 active execution
  -> H1/H2/H3 maintenance worker prepare + inactive preload
  -> compact ready handoff + boundary activation
  -> runtime observation and audit
  -> rollback + Compose cleanup + postflight
```

已验证运行是 `s4-1_0-compact-handoff-r141-long3-20260712`。

## 工作负载与生命周期

- S4 规划 shell，348 颗卫星；
- 28 UT、12 GW、14 zone；
- 四个 240 秒 horizon，每个 25 epoch，共 100 个 runtime 结果；
- 严格 `bpf_owned` 共享 G/Z，无 Linux/MPLS/NAT/Mini-Internet 转发回退；
- 三个 successor worker，各发布 25 个 inactive preload；
- 30 秒网关 service loopback 非阻塞观测。

## 必需验收事实

Profile 返回 0 且 `aggregate_runtime_succeeded=true`；H0–H3 各 25/25、无运行异常；三次 worker-ready 和 ready-consumed；每个 worker 25 个 inactive preload、零 blocker/禁止工作；H3 的物理 generation 0 被消费；BPF miss/drop 增量和 Linux fallback 为零；回滚完成；postflight 的容器、网络、主机接口、BPF pin、锁均为零；原始 ping timeout 保留为证据，不通过 fallback 修复。

## 廉价基线结果

```text
py_compile: PASS
unittest discover satellite/test_*.py: 791/791 PASS
new-package tests: stage 1 建立
```

建立结构起点时未使用 Compose、主机修改或长跑。机器可比较的 profile/输入指纹存于 `canonical_profile_snapshot.json`；绝对 run ID/输出路径被归一化，选择参数和已提交输入不被归一化。

## 已知文档漂移

仓库根 `README.md`、`WORKTREE_MAP.md` 当时仍引用已删除的 `build_topology.py`、`controller.py` 和旧 SEED `development2` 分支，不是 1.0 权威路线。Stage 5 新 CLI 成为标准时替换，历史命令不得复制到新用户指南。
