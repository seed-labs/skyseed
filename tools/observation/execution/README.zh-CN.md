# 实际采用的执行记录

[English](README.md)

`inspection.applied_events(plans, records)` 将保存的 PhysicalPlan 与 Runner 实际
回执关联，返回节点、链路目标、目的及按实际生效时间排序的指令。BPF 每节点代次更新、
同刻分批采用按回执的指令计数展开，未确认和未来指令不会被当成已生效状态。

输入为恢复的计划和从 JSONL 或 etcd 导出解码的执行记录。不运行模型、不启动测量、
不部署资源或写 Store。已采用指令能够解释状态，不能当作逐包经过的证据。

Scene、各层及计划可视化继续由各自 Tool 提供。S4 示例的 `experiment/observation/` 只补充其
临时 UT、流量及圆形干扰展示，不重复实现各层。
