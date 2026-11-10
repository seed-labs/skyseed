# Compiler 查看工具

[English](README.md) · **中文**

读取已保存的 PhysicalPlan，可选择 MPLS-like BPF／IP Linux、Link／Route、批次、节点，
以及起点／新增／上下文记录。链路图显示两端接口及各方向的延迟、带宽和丢包；
路由图显示“匹配 → 动作 → 输出”、局部标签、一跳出口绑定及缺链策略，
不据此推测实际连通性或从局部计划补画完整路径。

点击后端步骤查看具体动作和等价命令，点击端点查看服务／MAC 映射。
命令用于解释，不是完整 shell 脚本或执行日志；实际命名空间、接口编号和 BPF 句柄由 Runner 绑定。
撤销操作明确展示要删除的内容，起点／上下文与新增操作分开。原始字段和编码字节收在折叠详情中。

在仓库根目录使用当前开发环境：

```bash
python -m tools.observation.compiler.inspection output/bpf.plan output/linux.plan --output output/compiler
```

打开生成的 `index.html`。独立页面和 `compilation.json` 仅包含传入的计划，不运行模型、
重算位置或补算路线。缺少后端或筛选为空时明确显示无记录。
`instruction_display.py` 仅解释内置指令的展示含义；未知指令保留原始记录，不虚构动作。
编译入口与指令含义见 [模块说明](../../../new_src/skyseed/pc/compiler/README.zh-CN.md)。
