# Physical 物理结果检查

**中文** · [English](README.md)

查看真实的 [Physical](../../../src/skyseed/pc/physical/README.zh-CN.md) 结果：
位置快照、逐节点候选、距离、单向时延、仰角和自定义属性。
这是供开发与研究使用的可选中间结果工具，不负责选择连接或生成 LinkPlan。

直接读取已保存的观察，不运行 Physical 模型：

```python
from tools.observation.plan_views import write_physical
write_physical("output/observations/plans.json", "output/views/physical")
```

打开自包含的 `index.html` 即可离线查看，默认同图展示全部节点和所有分组的候选。
按星座、shell、轨道或地面站筛选只缩小查询节点列表，其他分组仍然显示。
再选择节点、切换时刻。点击候选淡线或表格对端可高亮，在图下查看属性和自定义字段。
所选对端在后续时刻消失时，会明确显示“已无该候选”。勾选“仅看当前节点与候选”可集中查看，
点击“全部同图”恢复全景。取消“显示所有分组的候选”可保留全部节点、只显示当前节点的候选。
分组只筛选查询节点，不丢弃报告中的真实对端；绘线时合并相反方向，报告仍分别保留，缺失值显示为未知。

Physical 展示模型采样的真实结果，不把几何圆轮廓强加到自定义轨迹上。

`inspection_data(physical, timestamps)` 接收已配置模型的 PhysicalLayer，支持自定义模型。
`write_inspection(data, output)` 写出 `physical.json`，并将相同事实嵌入页面。
工具要求自定义字段可序列化为 JSON，核心没有这项限制。这是计算端导出；
`write_physical` 只读取保存的上下文。
浏览器不重算模型，也不推测没有记录的候选拒绝原因。

`inspection.py` 负责采样导出与页面生成，`viewer.html` 负责结果视图。
页面与地球绘制共用 `../_presentation/` 中的资产；地球及附有来源说明的
Natural Earth 海岸线只用于显示，计算几何仍以模型为准。
