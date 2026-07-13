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

每次正常运行都会在 Compose 前，从该次运行冻结的 H0-H3 快照和物理链路自动生成
`output/<run-id>/visualization/index.html`。页面提供可旋转的 3D 地球和 96 帧时间轴，不再独立传播轨道或选择 anchor；源时间或 attachment 不一致会失败关闭，真实网络不会在错误投影后继续创建。

真实运行：

```bash
python skyseed_v1/examples/s5_operational_clean/run.py
```

真实运行进入 epoch 循环后，可在第二个终端直接演示实际选择的路径，不需要手动查容器名或地址：

```bash
python skyseed_v1/examples/s5_operational_clean/demo_ping.py
```

该动作直接读取示例中一次运行内不可变的 UT 网关规则和绑定服务地址，显示每条默认/特殊规则与当前切片，再对每条规则自动发送三次 ping。近期观察记录可补充动态服务卫星，但不是前置条件。增加 `--watch` 可持续演示到 Ctrl-C；等价的统一动作是 `run.py demo-ping`。探测在网络更新进程之外执行，网络更新不会等待它。

产物写到本示例的 `output/<run-id>/`。终端立即确认启动和路径，初始预览期间显示有界心跳，之后显示合并阶段变化及每个完成的 runtime epoch。控制台投影是独立观察者，不会延迟网络更新。清理完成后删除 `output/` 即可清空本示例全部生成状态。

## 成本形状

S5 预期 Compose 容器从 S4 的 416 个降到约 195 个，但并非所有阶段都更小：近极地几何在同一 960 秒窗口产生 399 个冻结跨轨候选 slot，而 S4 为 190；较小地面预设把 dummy-network 清单从 S4 的 631 降到 605。因此容器构建/启动应更低，但准备和清理不能简单按卫星数缩放。

已提交预设从声明开始时间起最多验证 7200 秒，所以 240 秒 horizon 下当前 `horizon_count=30` 为上限。更长或不同日期的实验必须重建/重新验证地面预设。当前仍要求固定 `loop_count`，尚不支持无限运行到 Ctrl-C。

源码中没有 `profile.json` 或场景期望文件；公共解析器会把实际 `effective_profile.json` 写入每个运行目录。
