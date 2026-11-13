# 可视化工具

[English](README.md)

当前工具独立于 Runner。Scene、逻辑计划、编译计划和绘图资料可以保存后再查看，
不需要保留 P&C 实例，也不启动 Docker、etcd 或流量程序。

```python
from tools.observation.plan_views import (
    load_context, write_scene, write_physical, write_link, write_network, write_compiler,
)

write_scene(scene_file, scene_output)
context = load_context(context_file)
write_physical(context, physical_output)
write_link(scene_file, base_link_file, execution_link_file,
           context=context, output=link_output)
write_network(scene_file, base_route_file, delivery_route_file, base_link_file,
              context=context, output=network_output)
del context
write_compiler(compiled_plan_file, compiler_output)
```

这些函数读取文件并返回生成的 `index.html`。Link／Network 按基础计划的分段逐页显示；
执行分支可以有不同分段，Delivery 重叠与最终延迟尾部保留。详细计划表仍保留原始批次。
调用示例见 [S4 view.py](../../examples/s4_handover/view.py)。页面自包含，可直接打开。

context 输入既可为文件路径，也可为 `load_context` 返回的字典。连续生成多个视图时，
加载一次并共享即可，页面生成只读取该字典；最后一个消费者用完后可释放它。
工具没有进程级缓存，再次加载文件会读取当时的内容。

| 输入 | 内容 |
|---|---|
| Scene 文件 | 场景组织、初始轨道与地面站、Zone 等端点资料 |
| LinkPlan 文件 | 基础／执行连接、方向属性及操作时刻 |
| RoutePlan 文件 | 基础／Delivery 逐节点规则、标签或 IP 动作 |
| PhysicalPlan 文件 | Compiler 生成的后端指令，和物理位置快照不同 |
| 绘图 context JSON | 保存的位置、Physical 快照与候选、可选模型观察 |

位置和候选由计算端显式保存，使用当时配置的 Physical 模型；读取端不重算几何、
候选、路由或服务分配。没有模型观察时，选择／关联／服务显示未知。缺少所需时刻的位置
会直接报错，不悄悄改用默认轨道。自定义观察字段须能编码为 JSON。

```python
from tools.observation.plan_views import save_context

save_context(
    context_file, physical,
    link_plans=all_link_plans, route_plans=all_route_plans,
    physical_times=sample_times,
    link_evaluations=link_evaluations, route_evaluations=route_evaluations,
)
```

`physical_times` 控制 Physical 页的候选采样；所有计划边界、操作和观察时刻的位置自动保留。
`position_times` 可另加位置采样，不额外查询候选。同一时刻的位置只导出一次。
这一步只保存数据；调用上面的 `write_*` 时才生成页面。

Plan 是正式计算产物，context 是可选 Tool 资料，两者不混成新的核心计划格式。
`save_context` 的观察适配针对现有 Link/GZ 默认模型；自定义工具可直接使用各层
`inspection_data`／`write_inspection`，不用模拟默认模型的诊断过程。

各页说明：[Scene](scene/README.zh-CN.md)、[Physical](physical/README.zh-CN.md)、
[Link](link/README.zh-CN.md)、[Network](network/README.zh-CN.md)、
[Compiler](compiler/README.zh-CN.md)。[Execution](execution/README.zh-CN.md)读取真实执行回执。
S4 的 UT、区域干扰和 TCP 回放属于该示例的额外展示。
