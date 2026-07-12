# S5 Operational-Clean 地面预设

`build.py` 根据标准规划 S5 轨道和现有地理候选目录生成已提交的 S5 地面预设。选择覆盖两小时、每 10 秒一帧（720 帧），因此不依赖默认四个 horizon 的较短实验窗口。

只有在冻结链路模型的每个分析帧中都可服务的网关或 UT 才会保留；策略随后依据保留的网关绑定重建，S5 运行时绝不使用 S4 策略文件。

从仓库根目录运行：

```bash
python skyseed_v1/tools/ground_presets/s5_operational_clean/build.py
```

该命令会确定性重写 `data/ground/` 下五个 `s5_operational_clean_*` 文件。提交轨道、链路模型或地面预设变更前，应审查差异并运行 S5 预览契约。
