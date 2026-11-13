# S4：构建、查看与运行

[English](README.md)

本例只有一套入口：348 颗 S4 卫星、32 个 GS、732 个全球 Zone，预计算 600 秒 BPF 标签计划，
再用临时 UT 和一条持续 TCP 连接观测换星及 60 秒区域断链。

| 文件 | 主要调用 | 产物 |
|---|---|---|
| [scene.py](scene.py) | 创建 Scene 并保存 | `output/scene/scene.scene` |
| [emu.py](emu.py) | SEED 配置、`dump`、`render`、`compile`、镜像构建 Tool | `output/seed/` |
| [simu.py](simu.py) | P&C、模型注册、两路计划、Compiler；最后保存可选绘图资料 | `output/plans/`、`observations/`、`build.json` |
| [view.py](view.py) | 标准 Tool 读取保存文件，逐层生成页面 | `output/views/`、`index.html` |
| [run.py](run.py) | Store、Deployment、Runner，接入 UT，最后归档并生成结果 | `output/runs/run-时间/` |

`scene.py` 读取 S4 的[参数预设](../../data/README.zh-CN.md)，显式调用
`Scene.create_shell`：6 条轨道、每轨 58 星。修改 shell 名称及
`satellites_per_plane` 为 59 或 60 即可构造自定义布局；GS 仍逐个读取实际位置资料。
保存的 `.scene` 包含生成后的完整场景，Emu 和 P&C 直接复用同一套身份与几何，无需重新生成。

在仓库根目录的开发环境中：

```bash
python -m examples.s4_handover.scene
python -m examples.s4_handover.emu
python -m examples.s4_handover.simu
python -m examples.s4_handover.view
# 查看计划后，需要真实运行时再执行：
python -m examples.s4_handover.run
```

`view` 不启动容器、etcd、P&C 或测量；重复执行只读取产物并生成页面。
`run` 启动真实网络；重复运行无需重新计算，运行要求见
[Runner](../../src/skyseed/runner/README.zh-CN.md)。修改场景后需重新构建匹配产物，
SEED 不覆盖已有部署目录，另一组构建应选择新的输出目录。

[手动测试、换星观察及快捷命令](MANUAL.zh-CN.md)。默认循环运行，Ctrl+C 清理并保存结果。

## 正式接口与本例扩展

`from skyseed import ...` 和 `from seedemu import ...` 是正式接口。
`tools.deployment.build_images` 与 `tools.observation.plan_views` 是可独立调用的仓库工具。
[experiment/](experiment/README.zh-CN.md)只保存本例辅助实现，没有第二份 Simu／Run：

- `ut/`：临时 UT 接入、GS 的 UT 前缀规则和 TCP 程序。
- `perturbation.py`：圆形区域 GS 断链 Model。
- `record_store.py`：本例 etcd 服务托管、冻结输入和测量归档；`Store` 本身仍是正式接口。
- `configuration.py`、`observation/`：实验设置摘要、特定业务结果、UT／干扰叠加和导航页。

主流程显式展示扩展的注册和接入。`simu.py` 中 `list(generate_*_plans(...))` 消费迭代器，
实际完成请求；`timedelta(seconds=60)` 是 Python 的时长，不表示模型每 60 秒才变化。
`run.py` 中 `with` 是 Python 的资源管理语法，退出时按 UT、Runner、部署、Store 的顺序释放。

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
  runs/run-时间/                  # 冻结输入、运行记录、测量、结果页面
  index.html
```

固定配置、包的标签路径、干扰原理及结果解释见 [实验说明](experiment/README.zh-CN.md)。
