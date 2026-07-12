# 示例

示例是面向用户、可直接执行的组合，不是 CLI 实现或测试夹具。每个示例拥有一个可编辑的 `experiment.py`、一个简短的 `run.py`、说明文件以及被忽略的 `output/` 目录。

当前可执行示例：

- `s4_operational_clean/`：已验收的完整 S4 结构切换基线；
- `s5_operational_clean/`：使用相同地面/运行时组件的完整 S5。

每个目录都提供 `run.py inspect`、`preflight`、`preview`，默认动作是真实 `run`。新实验应复制整个示例目录。解析器会把 `effective_profile.json` 写入 `output/<run-id>/`。
