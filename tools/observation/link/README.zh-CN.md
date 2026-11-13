# Link 计划检查

**中文** · [English](README.md)

查看真实 [Link](../../../src/skyseed/pc/link/README.zh-CN.md) 选择、准备时刻、
可用连接，以及基础／执行分支差异。

使用[保存产物查看工具](../README.zh-CN.md)的 `write_link`，传入 Scene、两份 LinkPlan
和绘图 context。工具不再内置演示场景或重新规划，直接打开生成的 `index.html`。

- 拖动地球，点击节点、连接或精确事件查看详情。
- 选择只高亮，勾选“仅看所选”才隔离；“全部同图”恢复全景。
- 打开候选线，对比物理机会和实际选择。
- 切换基础／执行／对照，粉色表示可用性或属性有差异。
  单分支视图只以该分支判断可用；候选开关仅叠加已记录的物理机会，不把机会画成可用连接。
- 时间轴包括实际操作时刻；详情保留双向物理报告、有效属性与自定义字段。
  未提供求值记录时显示未知。候选明确标注最近一次记录的求值时间，
  不把它冒充为中间时刻重新查询的结果。
- **计划详情**进入 `plans/index.html`，带上当前连接／节点、时刻和分支（对照视图进入执行分支）。
  可切换批次，筛选起点状态或新操作，展开完整字段，也可从一行记录返回图形。
  图形显示该时刻应用操作后的状态；起点前记录返回起点时，图上包含该刻操作的影响。
  下载 `base_link.plan` 或 `execution_link.plan` 可复用原始计划批次。

`inspection_data(scene, base_plans, execution_plans, positions=..., evaluations=..., timestamps=...)`
读取计划、保存的位置映射和可选 JSON 观察，不调用 Physical。`timestamps` 可增加已有位置的
显示时刻；计划事件始终保留。`write_inspection` 写出自包含页面及 `link.json`。
标准文件入口还生成原始批次的计划详情和分段导航。
