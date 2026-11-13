# 使用 SkySEED

[English](usage.md) · [接口速查](reference.zh-CN.md)

先完成[安装](../README.zh-CN.md#安装)。仓库示例和 Tools 在配置好的 Python 环境中，
从仓库根目录运行。
可选的 [Shell 快捷命令](../tools/README.zh-CN.md#快捷命令)提供 `skyseed`、`docksh`、
Docker Compose 操作和 S4 运行查询。

## 分别构建

| 阶段 | 用户配置 | 可复用结果 |
|---|---|---|
| Scene | 节点、分组、轨道输入、GS 与端点 | `Scene.dump(...)` / `Scene.load(...)` |
| Emulator | AS 分配、SEED 网络、软件和服务 | 未 render 的 SEED 描述；render 后的部署文件 |
| P&C | 模型及规划区间 | 基础／执行 LinkPlan；基础／Delivery RoutePlan |
| Compiler | 部署目录与转发后端 | 包含后端指令的 PhysicalPlan |

Scene 可独立于 Emulator 和 P&C 使用；Emulator 建节点不需要计划，逻辑规划也不需要部署。
**编译计划**时才读取部署文件分配稳定目标，Runner 再解析本次运行的实际资源。

[小网络](../examples/tiny_network/README.zh-CN.md)展示 Scene 和 SEED 构建。
[S4](../examples/s4_handover/README.zh-CN.md) 分为 `scene.py`、`emu.py`、`simu.py`、
`view.py`、`run.py`；UT、干扰和测量是单独列出的示例扩展。运行完整实验前先查看其配置。

## 生成与保存计划

已有 Scene 和对应的 SEED 部署文件时：

```python
from datetime import timedelta
from skyseed import Scene, PlanningControl, PlanCompiler, save_plans, load_plans

scene = Scene.load("output/scene/scene.scene")
pc = PlanningControl(scene)
start = scene.get_shells()[0].epoch
end = start + timedelta(seconds=60)

save_plans("output/plans/links.plan", pc.generate_link_plans(start=start, end=end))
save_plans("output/plans/routes.plan", pc.generate_route_plans(start=start, end=end))

compiler = PlanCompiler("output/seed/deployment", forwarding_backend="bpf")
save_plans("output/plans/physical.plan", compiler.compile_plans(
    link_plans=load_plans("output/plans/links.plan"),
    route_plans=load_plans("output/plans/routes.plan"),
))
```

`timedelta` 是 Python 标准的时间长度类型。为实验选择一个带时区的起点；
若希望从某个 shell 的历元开始，可以像上面这样读取。Scene 不要求所有位置来源共用一个历元。

消费返回的迭代器时才执行计算。Link 与 Route 请求共用基础链路计算，但输出分开。
后续区间从上次请求终点连续向前；Delivery 延迟尾部自动包含，不改变下次请求起点。
独立实验使用新的模型实例。计划文件保存结果值，不是活着的模型的检查点。
接续和文件细节见 [P&C](../src/skyseed/pc/README.zh-CN.md)。

默认路由模型生成 MPLS-like 标签。使用普通 IPv4/Linux 时，为
`GroundStationZoneRoutingModel` 设置 `label_forwarding=False`，并为 Compiler 设置
`forwarding_backend="linux"`；Compiler 不会悄悄把标签动作改成 IP 规则。

## 替换模型

在生成计划前调用所属 Layer 的 setter。自定义类只需实现该接口要求的最低方法，
不强制继承默认实现；构造参数通过 `model_options` 传给模型自身。

```python
from skyseed.pc.link import PeriodicLinkPlanModel

link = pc.get_layer("link-layer")
link.set_plan_model(PeriodicLinkPlanModel,
                    model_options={"interval": timedelta(seconds=10)})
```

[模型速查](reference.zh-CN.md#模型)链接最低接口与默认实现。
Physical 的位置和候选模型必须存在、可以替换。Perturbation 是可选 hook，
修改执行链路分支，不改变用于路由计算的基础链路。Delivery Scheduling 调整路由采用时间。
示例中的[区域中断模型](../examples/s4_handover/experiment/perturbation.py)展示具体编辑方式，
不要求使用通用干扰框架。

## 查看已有结果

[Tools](../tools/observation/README.zh-CN.md)读取保存的 Scene、Plan 和可选绘图观察。
计算端使用 `save_context` 保存位置与模型观察，查看端不重跑模型。

```python
from tools.observation.plan_views import write_scene, write_compiler

write_scene("output/scene/scene.scene", "output/views/scene")
write_compiler("output/plans/physical.plan", "output/views/compiler")
```

直接打开返回的 `index.html`。Link／Network／Physical 页面还需要保存的绘图上下文，
参见 [S4 view.py](../examples/s4_handover/view.py)。Tools 是仓库工具，不包含在核心 wheel 中。

## 运行与清理

```python
from skyseed import Deployment, Runner, Store, load_plans

deployment = Deployment("output/seed/deployment")
store = Store("http://127.0.0.1:2379", namespace="skyseed/run-001")
with deployment:
    with Runner(load_plans("output/plans/physical.plan"), deployment,
                output="output/runs/run-001", store=store) as runner:
        runner.run()
```

运行前先构建部署镜像。这里假定 etcd 已启动，由应用负责启停；S4 的 `emu.py` 构建镜像，
`run.py` 通过示例辅助管理本次 etcd。`with` 是 Python 的资源上下文用法：
Deployment 负责 Compose 启停，Runner 负责执行资源准备和释放。
有限输入自然完成时，`run()` 在计划及延迟尾部执行完后返回；主动调用 `Runner.stop()`
则记录 `stopped`，尚未执行的工作不计为完成。离开上下文时才清理，生命周期见
[Runner](../src/skyseed/runner/README.zh-CN.md#生命周期与时间)。更新晚到不会平移计划时间轴。

Store 提供进程间共享状态及带 revision 的订阅。Runner 同时写 `execution.jsonl`；
不需要共享访问的独立运行可省略 Store。离线分析直接读取文件，不需要 etcd。
每次使用新的运行目录，不复用前次的活资源身份。

若使用仓库 Tool `build_images` 构建复用镜像，Deployment 须指定
`compose_file="docker-compose.runtime.yml"`，与 S4 的 `run.py` 一致。
默认文件名 `docker-compose.yml` 对应 SEED 原始部署文件。

## 运行环境

现场例子使用 Linux 宿主及本机 Docker Engine；Docker Desktop 的虚拟机或远程
Docker daemon 不共享 Runner 所需的宿主命名空间。Ubuntu 24.04 可按
[Docker Engine 与 Compose 官方说明](https://docs.docker.com/engine/install/ubuntu/)
安装，并配置[普通用户访问](https://docs.docker.com/engine/install/linux-postinstall/)。
在运行 Python 的同一终端检查访问并安装系统依赖：

```bash
docker info
docker compose version
sudo apt update
sudo apt install clang linux-libc-dev libbpf1 iproute2 tcpdump
```

`clang` 和 Linux 头文件用于编译 BPF，`libbpf1` 用于加载；`iproute2` 提供网络工具，
S4 的 UT 辅助程序使用宿主 `tcpdump` 抓包。系统库与工具已有、仅缺 clang 时，
也可在活动环境执行 `conda install -c conda-forge clang`，无需改动宿主安装。
构建完整例子前可先验证 BPF 编译，不启动容器：

```bash
python -c 'from skyseed.pc.compiler.bpf.compile_program import compile_label_program; print(len(compile_label_program()), "BPF ELF bytes")'
```

首次镜像构建需要访问 SEED 镜像源及软件包仓库，后续可复用 Docker 缓存。
S4 首次运行还会拉取 etcd 镜像，不要求事先启动 etcd 服务。

- Linux 的 veth、namespace、TC/netem；Docker Compose，以及运行特权 Docker 容器的权限。
- BPF 模式还需要支持 BPF target 的 clang、Linux/BPF 开发头文件和 libbpf。
  Runner 在正式起钟前编译随包提供的程序。
- 节点使用 SEED 提供的常规工具和软件。BPF 本地产生流量的注入需要节点设置关闭
  `all.rp_filter`，细节见 [Runner](../src/skyseed/runner/README.zh-CN.md)。
- Store 需要可用的 etcd v3 服务；S4 的辅助启动 `quay.io/coreos/etcd:v3.6.5` 容器，
  绑定本机随机端口，归档后停止服务。

当前完整示例先预计算再运行。接口可接续有限请求和计划批次，但没有内置多机调度器。
可见性、路径存在性和应用连续性由场景与模型决定。计划路径、实际采用规则推导的路径、
抓包观察到的报文分别表达不同证据。
