# Stage 5 验收：纯 CLI 和标准示例

状态：**PASS**

本门禁未启动 Compose、主机网络修改、BPF 挂载或真实长跑。

## 入口边界

安装命令为：

```bash
skyseed-v1 run --example s4_operational_clean [options]
```

`cli.main` 只负责参数解析、示例选择和选项转发；`cli.experiment` 是轻量可执行包装；实验/profile 编排位于 `runtime/`。`satellite/apps/` 下两个旧路径只是最多 25 行、无类和业务策略的临时桥，不是用户文档入口。

## 命令等价

标准 dry-run 选择 `rich_s4_operational_clean_bpf` 并通过目录和 Docker 地址容量门禁。唯一有意变化是 runner 文件路径：恢复旧路径后命令 hash 与基线 `a1e9c...` 完全相同；新标准命令 hash `2eb439...`；Python/runner 后的语义 argv hash `4fa5af...`。Profile hash `c4efe9d2...` 和 catalog hash `d1290ba4...` 不变。

## 重放和检查

入口切换后冻结消费者 corpus：100/100，H0–H3 各 25，四个 prepared horizon、1005 binding 引用、100 `shared_gz_noop_hold`，零 blocker/retired IX，131.79 秒。

- 新包 23/23（含四个 CLI 边界测试）；
- 入口/profile/runtime 回归 299/299；
- 完整旧套件 791/791；
- `skyseed-v1 list-examples`、标准和旧桥 profile dry-run 均通过。

Stage 5 建立一条干净用户路线且不改变运行请求。可观测性、审计、SEED adapter、运维工具、数据归属和临时 alias 的最终退役留到 Stage 6/7。
