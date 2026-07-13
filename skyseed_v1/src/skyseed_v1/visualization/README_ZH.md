# 可视化

负责面向展示的物理链路和导出格式。可视化消费逻辑/运行时证据；除非 profile 明确冻结某项输入契约，否则它不对后端行为具有权威性。

## 外部集成边界

- `export_links.py` 生成可视化项目消费的稳定 `groundLinks` 与 `satelliteLinks` 组合切片；
- `export_ground_links.py` 和 `export_satellite_links.py` 提供组合导出器使用的两类专用生产器；
- `export_planned_shell_orbit_data.py` 只在有意更新契约时重新生成共享、确定性的轨道交换文件；
- 已提交的轨道和网关输入位于 `data/orbits/`、`data/ground/`，生成的链路切片属于示例自己的 `output/`。
- `demo_globe.py` 在每次 run directory 内生成独立时间线页面。它直接读取该次运行已经冻结的快照和物理链路，校验源时间及 attachment/anchor 身份，自身不执行任何规划。

这些导出器只消费拓扑数据，永不控制后端或运行时更新。接口用法和 schema 见 `docs/interfaces/visualization/`。
