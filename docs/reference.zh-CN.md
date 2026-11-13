# 接口与产物速查

[English](reference.md) · [使用指南](usage.zh-CN.md)

本页帮助定位公开操作；准确签名、字段、单位和最低合同由链接的模块维护。
私有 helper 不属于自定义模型必须实现的要求。

| 需要做什么 | 入口 | 详细说明 |
|---|---|---|
| 构造／查询分组、节点和端点 | `Scene.create_*`、`get_*`、`nodes`、`groups`、`endpoints` | [Scene](../src/skyseed/scene/README.zh-CN.md) |
| 按参数生成圆轨道 shell | `Scene.create_shell(..., planes=..., satellites_per_plane=...)` | [几何与相位](../src/skyseed/scene/README.zh-CN.md) |
| 保存／读取场景事实 | `Scene.dump`、`Scene.load` | [Scene](../src/skyseed/scene/README.zh-CN.md) |
| 加载到 AS、配置 SEED 节点 | `SkyAS.loadScene`、`getNode`、`getSceneNodes` | [Emulator](../src/skyseed/emulator/README.zh-CN.md) |
| 计算位置或候选连接 | `PhysicalLayer.snapshot_at`、`candidates`、`candidates_at` | [Physical](../src/skyseed/pc/physical/README.zh-CN.md) |
| 查询链路历史、试算连接 | `LinkLayer.base_plan`、`base_plans`、`execution_plans`、`evaluate_connections` | [Link](../src/skyseed/pc/link/README.zh-CN.md) |
| 查询路由或服务分配 | `NetworkLayer.base_plans_between`、`delivery_state_at`、`service_assignments_at` | [Network](../src/skyseed/pc/network/README.zh-CN.md) |
| 请求有限区间的计划 | `PlanningControl.generate_link_plans`、`generate_route_plans` | [P&C](../src/skyseed/pc/README.zh-CN.md) |
| 查询已保存的逻辑计划 | `state_at`、`states_at`、`slice`、`end_links` / `end_rules` | [Link](../src/skyseed/pc/link/README.zh-CN.md)、[Network](../src/skyseed/pc/network/README.zh-CN.md) |
| 保存／恢复计划 | `save_plans`、`load_plans` | [计划文件](../src/skyseed/pc/README.zh-CN.md) |
| 编译两路计划 | `PlanCompiler.compile_plans` | [Compiler](../src/skyseed/pc/compiler/README.zh-CN.md) |
| 启停容器、执行计划 | `Deployment.up/down`、`Runner.prepare/run/close` | [Runner](../src/skyseed/runner/README.zh-CN.md) |
| 共享数据或订阅变化 | `Store.put/get/list/put_many/watch`、`put_user/get_user` | [Store 用法](../src/skyseed/runner/README.zh-CN.md) |
| 将已保存的数据导出为页面 | `tools.observation.plan_views.write_*` | [观察工具](../tools/observation/README.zh-CN.md) |

读取计划不重算模型；位置、候选和服务查询会调用对应模型；连接试算不会采用其选择。
生成方法推进保留的规划状态；部署和执行方法才改变运行网络。组合调用前查看所属模块的生命周期说明。

## 模型

Setter 接收类及可选构造参数 `model_options`，Layer 保留实例。
最低方法的具体参数与返回值见所属模块。

| 所属模块 | Setter | 最低方法 | 内置行为 |
|---|---|---|---|
| [Physical](../src/skyseed/pc/physical/README.zh-CN.md) | `set_position_model` | `compute_positions` | 圆轨道；TLE 可用 SGP4 |
| Physical | `set_candidate_model` | `compute_candidates` | 几何候选 |
| [Link](../src/skyseed/pc/link/README.zh-CN.md) | `set_selection_hook` | `select_links` | 优先保留仍可行连接，再填充预算 |
| Link | `set_property_hook` | `adjust_properties` | 可选属性调整 |
| Link | `set_plan_model` | `generate_plan` | 周期采样与建链准备 |
| Link | `set_perturbation_hook` | `perturb` | 可选执行分支修改 |
| [Network](../src/skyseed/pc/network/README.zh-CN.md) | `set_service_assignment_model` | `assign_service` | 最高仰角服务卫星 |
| Network | `set_routing_hook` | `compute_routes` | GZ 路由，默认 MPLS-like，也可生成 IP 规则 |
| Network | `set_plan_model` | `generate_plans` | 周期路由更新 |
| Network | `set_delivery_scheduling_hook` | `schedule_plans` | 可选交付编排，提供固定延迟实现 |

共同路由组织与默认转发规则分别定义。研究元数据可扩展；新的匹配／动作语义需要对应
Compiler 和执行器支持，任意 `data` 字段不会自动改变报文处理。

## 产物

下列名称以 S4 布局为例，核心保存接口接受调用方指定的路径。

| 产物 | 内容 | 复用方式 |
|---|---|---|
| `scene/scene.scene` | Scene 身份、分组、输入与端点 | 不依赖 P&C／SEED 读取 |
| `seed/emulator.bin` | 未 render 的 SEED 描述 | 加载、修改后再 render |
| `seed/deployment/` | SEED Compose、Dockerfile 和节点配置 | 编译目标及部署 |
| `plans/logical/*.plan` | 基础／执行链路、基础／Delivery 路由 | 查询、编译、对比 |
| `plans/physical/*.plan` | 后端目标和定时指令 | 在匹配部署上执行 |
| `observations/plans.json` | 绘图采样及可选模型决策 | 无模型实例也能生成页面 |
| `views/` | 自包含页面 | 离线打开 |
| `runs/<run>/` | 冻结输入、实际执行及示例测量 | 分析这一次运行 |

Physical 的位置**快照**是模型结果，Physical**Plan** 是编译指令。
Store 共享运行状态，导出的 JSON／JSONL 保留离线记录。
具体字段分别由 [P&C](../src/skyseed/pc/README.zh-CN.md)、
[Compiler](../src/skyseed/pc/compiler/README.zh-CN.md)、
[Runner](../src/skyseed/runner/README.zh-CN.md) 说明。
