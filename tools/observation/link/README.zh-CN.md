# Link 计划检查

**中文** · [English](README.md)

查看真实 [Link](../../../new_src/skyseed/pc/link/README.zh-CN.md) 选择、准备时刻、
可用连接，以及基础／执行分支差异。

```bash
python -m tools.observation.link.inspection --output /tmp/skyseed-link-view
```

在仓库根目录的 `skyseed_dev` 环境运行，离线打开生成的 `index.html`。
与 [Scene 工具](../scene/README.zh-CN.md) 共用 64 颗参数化卫星和三个城市示例点。
演示每 5 秒求值，特意使用 **7.5 秒准备时间**展示采样点之间的建立事件，
不是新的核心默认值。没有扰动时两个分支一致。
通过 `--scene`、`--position-model sgp4`、`--start`、`--seconds`、`--setup-seconds`
改变输入；自定义模型由 Python 配置。

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

`inspection_data(physical, base_plans, execution_plans, evaluations=..., timestamps=...)`
读取真实连续计划和可选的 `LinkEvaluation`；只为图中位置查询 Physical，
不重新运行选择、生成或 Perturbation。升序状态查询一次遍历计划操作。
`timestamps` 用于增加显示采样；区间起点、实际操作和已记录求值时刻始终保留。
`write_inspection` 写出 `link.json` 和共用地球资产的自包含页面。
导出的自定义字段须能序列化为 JSON。演示还调用
`tools.observation._presentation.plan_details` 中共用的
`write_plan_details(output, kind="link", streams={"base": ..., "execution": ...})`，
并在写主页面前设置 `data["plan_details"] = "plans/index.html"`。
详情表读取保存后恢复的计划，并逐份与原始值比较。
计划文件保存逻辑计划；几何及求值观察资料仍由主页面的展示数据提供。
分别报告规划、展示采样、计划文件／详情及主页面写出成本；工具整体整合后置。
