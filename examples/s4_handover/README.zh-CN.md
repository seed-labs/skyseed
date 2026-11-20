# S4：构建、查看与运行

[English](README.md)

本例以 S4 为参考配置，可更换壳层复用同一换星实验流程。
默认配置包含 348 颗 S4 卫星、32 个 GS、732 个全球 Zone，预计算 600 秒 BPF 标签计划，
再接入临时 UT，供用户手动通信、观测换星及 60 秒区域断链。

| 文件 | 主要调用 | 产物 |
|---|---|---|
| [scene.py](scene.py) | 创建 Scene 并保存 | `output/scene/scene.scene` |
| [emu.py](emu.py) | SEED 配置、`dump`、`render`、`compile`、镜像构建 Tool | `output/seed/` |
| [simu.py](simu.py) | P&C、模型注册、两路计划、Compiler；最后保存可选绘图资料 | `output/plans/`、`observations/`、`build.json` |
| [view.py](view.py) | 标准 Tool 读取保存文件，逐层生成页面 | `output/views/`、`index.html` |
| [run.py](run.py) | Deployment、Runner 和临时 UT | `output/runs/run-时间/` |

`scene.py` 的 `SHELLS = ("S4",)` 选择[参数预设](../../data/README.zh-CN.md)。
改成 `("S5",)` 或 `("S1", "S2", "S3")` 即可复用同一流程，无需复制另一个例子。
每组预设完整传给 `Scene.create_shell`，包含 S5 的非均匀 RAAN 和相位参数。
自定义轨道参数也在这里调整。GS 继续读取实际站点资料，保存的 `.scene` 由后续阶段共用。
操作手册中的具体时间和路径对应 S4。

完成[开发环境安装](../../README.zh-CN.md#安装)后执行以下步骤；本机已配置 `skyseed`
快捷入口时先执行它即可。已有环境升级需在仓库根目录执行一次 `python -m pip install -e ./tools`。

在仓库根目录的开发环境中：

```bash
cd examples/s4_handover
python scene.py
python emu.py
python simu.py
python view.py
# 查看计划后，需要真实运行时再执行：
python run.py
```

`simu.py` 会即时显示当前阶段，以及每个 60 秒规划区间的进度。
区间数字表示仿真时间，`elapsed` 表示实际计算耗时。
`view.py` 显示正在生成哪一层页面，完成后给出 `output/index.html` 入口。

`view` 不启动容器或 P&C；重复执行只读取产物并生成页面。
`run` 启动真实网络；重复运行无需重新计算，运行要求见
[Runner](../../src/skyseed/runner/README.zh-CN.md)。修改场景后需重新构建匹配产物，
先停止旧实验，并将本例目录下的旧 `output` 改名保存（例如 `mv output output-s4`），
再从 `scene.py` 开始重建。较大星座会增加计算和运行资源；换星时刻、覆盖和跳数需按新场景观察。
默认 S4 的手册示例不代表 S5 或多壳层具有相同路径。

[手动测试、换星观察及快捷命令](MANUAL.zh-CN.md)。默认循环运行，Ctrl+C 清理。
也可进入本例目录直接执行 `python run.py`。

## 正式接口与本例扩展

`from skyseed import ...` 和 `from seedemu import ...` 是正式接口。
`tools.deployment.build_images` 与 `tools.observation.plan_views` 是可独立调用的仓库工具。
[experiment/](experiment/README.zh-CN.md)只保存本例辅助实现，没有第二份 Simu／Run：

- `ut/`：临时 UT 接入和 GS 的 UT 前缀规则。
- `perturbation.py`：圆形区域 GS 断链 Model。
- `configuration.py`、`observation/`：实验设置摘要和离线计划导航。
- `console.py`、`repetition.py`：运行状态和已有计划循环。

主流程显式展示扩展的注册和接入。`simu.py` 中 `list(generate_*_plans(...))` 消费迭代器，
实际完成请求；`timedelta(seconds=60)` 设置每次规划请求的时长。
`run.py` 中 `with` 是 Python 的资源管理语法，退出时按 UT、Runner、部署的顺序释放。

## 保存产物与 Tool

```python
from tools.observation.plan_views import write_link

write_link(
    scene_file, base_link_file, execution_link_file,
    context=observation_file, output=view_directory,
)
```

Scene 可直接查看；Link／Route 的 3D 页面还需要位置，候选／服务等中间过程需要可选观察记录。
`simu.py` 在计算阶段显式调用 `save_context` 保存这些资料；重新查看时不重算模型。
观察文件只用于展示，正式计划仍是 `.plan`。Physical 页读取位置／候选快照，
Compiler 页读取后端 PhysicalPlan，两者含义不同。完整独立用法见
[可视化工具](../../tools/observation/README.zh-CN.md)。

```text
output/
  scene/scene.scene
  seed/{emulator.bin,deployment/}
  plans/{logical/,physical/bpf.plan}
  observations/plans.json        # 几何和可选模型观察
  build.json                     # 本例实验设置
  views/{scene,physical,link,network,compiler}/
  runs/run-时间/                  # 执行、接入状态和诊断日志
  index.html
```

固定配置、包的标签路径、干扰原理及手动观察见 [实验说明](experiment/README.zh-CN.md)。
