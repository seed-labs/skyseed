# S5 Operational-Clean 示例

完整环境、主机迁移和运行时要求见 `../../docs/operations/environment_and_running_ZH.md`。

本示例选择完整 S5 shell：4 个轨道面 × 每面 43 颗 = 172 颗卫星，并保留已验证 S4 示例的其他原则：

- 经过冻结两小时窗口筛选的 9 个 UT、5 个网关、6 个 zone；
- 4 个 240 秒 horizon；
- 10 秒物理采样与事件判断；
- 严格 `bpf_owned` 转发和 `operational_clean` 运行时；
- 滚动进程 worker 准备 horizon，并在外部验证零残留。

S5 地面预设独立于 S4 运行输入。候选在声明的实验开始时间之后 720 个十秒帧上判断，只保留每帧都可服务的 GW/UT，再重建 S5 专属 zone 绑定和 UT 网关策略。两小时是明确的资格范围，不表示相对地球连接会永久周期重复。所有用户选择只在 `experiment.py` 声明一次。

## 运行

从仓库根目录执行廉价检查：

```bash
python skyseed_v1/examples/s5_operational_clean/run.py inspect
python skyseed_v1/examples/s5_operational_clean/run.py preflight
python skyseed_v1/examples/s5_operational_clean/run.py clean
python skyseed_v1/examples/s5_operational_clean/run.py preview
```

`preflight` 用 `ready=True` 或 `ready=False` 开头输出简短摘要；完整审计 payload 使用 `--format json`。`clean` 只清理 Skyseed 归属残留并要求之后 ready，有活跃进程时拒绝运行。

真实运行：

```bash
python skyseed_v1/examples/s5_operational_clean/run.py
```

产物写到本示例的 `output/<run-id>/`。终端立即确认启动和路径，初始预览期间显示有界心跳，之后显示合并阶段变化及每个完成的 runtime epoch。控制台投影是独立观察者，不会延迟网络更新。清理完成后删除 `output/` 即可清空本示例全部生成状态。

## 成本形状

S5 预期 Compose 容器从 S4 的 416 个降到约 195 个，但并非所有阶段都更小：近极地几何在同一 960 秒窗口产生 399 个冻结跨轨候选 slot，而 S4 为 190；较小地面预设把 dummy-network 清单从 S4 的 631 降到 605。因此容器构建/启动应更低，但准备和清理不能简单按卫星数缩放。

已提交预设从声明开始时间起最多验证 7200 秒，所以 240 秒 horizon 下当前 `horizon_count=30` 为上限。更长或不同日期的实验必须重建/重新验证地面预设。当前仍要求固定 `loop_count`，尚不支持无限运行到 Ctrl-C。

源码中没有 `profile.json` 或场景期望文件；公共解析器会把实际 `effective_profile.json` 写入每个运行目录。
