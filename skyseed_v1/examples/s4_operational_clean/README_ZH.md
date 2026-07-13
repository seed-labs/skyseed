# S4 Operational-Clean 示例

完整环境、主机迁移和运行时要求见 `../../docs/operations/environment_and_running_ZH.md`。

本目录是独立、SEED 风格的 Satellite Internet 1.0 已验证场景组合根：

- 规划 S4 shell，348 颗卫星；
- 28 个 UT、12 个网关、14 个 zone；
- 4 个 240 秒 horizon，10 秒采样；
- 严格 `bpf_owned` 共享 G/Z 转发；
- operational-clean 滚动维护与非阻塞观测。

`experiment.py` 是唯一可编辑定义，选择 shell、地面预设、horizon、采样、运行时、开始时间和输出目录。平台实现保留在示例外。

S4 是公开的星座选择；348 颗卫星由 shell 定义派生，不是独立规模旋钮。未来若建立缩小实验，必须明确命名均衡采样策略，不能用 `max_satellites` 静默截断。

主要入口刻意保持简短：

```bash
python skyseed_v1/examples/s4_operational_clean/run.py
```

命令把生成输入和审计产物写入本示例的 `output/<run-id>/`。终端立即确认启动和输出路径，初始预览期间输出有界心跳，持久快照产生后合并展示阶段/epoch 进度。长期不变阶段继续输出耗时和可用阶段细节。投影是独立 daemon 观察者，网络更新绝不等待终端输出。

同一文件提供廉价动作：

```bash
python skyseed_v1/examples/s4_operational_clean/run.py inspect
python skyseed_v1/examples/s4_operational_clean/run.py preflight
python skyseed_v1/examples/s4_operational_clean/run.py clean
python skyseed_v1/examples/s4_operational_clean/run.py preview
```

`inspect`、`preview` 不创建真实网络；`preflight` 只探测主机就绪并输出简短摘要，完整审计证据使用 `preflight --format json`；`clean` 只删除 Skyseed 归属残留并要求清理后预检 ready，有 Skyseed 进程活跃时会拒绝执行。

每次正常运行会在全部 rolling-horizon 输入冻结后、Compose 启动前，自动生成自己的离线页面
`output/<run-id>/visualization/index.html`。浏览器直接打开后，可以旋转、缩放地球、拖动或播放时间轴，并分别开关卫星、UT、GW、zone、ISL、规范 UT→zone anchor attachment 和逻辑 UT→GW 策略。页面直接读取该次运行实际消费的冻结快照与物理链路，不再独立传播轨道或选择 anchor；时间或 attachment 不一致会在 Compose 前令运行失败。

真实运行进入 epoch 循环后，在第二个终端执行：

```bash
python skyseed_v1/examples/s4_operational_clean/demo_ping.py
```

它直接读取示例中一次运行内不可变的 UT 网关策略和 GW 服务地址，显示默认/特殊规则与当前切片，再从该 UT 容器对每条规则自动发送三次 ping。若已有近期观察记录，还会补充动态服务卫星，但观察记录不是 ping 的前置条件。`demo_ping.py --watch` 会持续演示到 Ctrl-C；`demo_ping.py --ut ut2014` 可指定 UT。等价的统一动作是 `run.py demo-ping`。演示会产生探测流量，但不会阻塞网络更新进程。

把 `RuntimeConfig(sample_interval_seconds=10)` 改为 `20` 会自动更新事件判断和可视化采样。解析配置写入 `output/<run-id>/effective_profile.json`。

自动化和特殊 run root/ID 可使用参数 CLI：

```bash
skyseed-v1 run \
  --example s4_operational_clean \
  --run-id inspect-s4 \
  --dry-run \
  --format text \
  --progress off
```
