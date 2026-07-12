# 可视化集成契约

本目录描述 Skyseed 与独立维护的可视化项目之间的文件边界。Skyseed 负责确定性的轨道/地面输入和链路切片；可视化系统消费这些文件，不参与网络规划或运行时激活。

标准输入：

- `data/orbits/planned_shell_orbit_data_s1_s5.json`；
- `data/ground/starlink_gateways.json`；
- 实验生成的 `output/<run-id>/.../visualization_physical_links.json`。

统一 payload schema 见 `export_links_ZH.md`。用于接口调试的地面和卫星专用导出器仍保留。修改边界必须显式升级 schema/版本并增加集成契约测试，不能悄悄改变运行时网络语义。
