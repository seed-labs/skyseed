# 按职责组织测试

[English](README.md)

| 位置 | 验证内容 |
|---|---|
| `unit/scene`、`unit/emulator` | 场景身份／几何／持久化、SEED 节点构建与归属 |
| `unit/physical`、`unit/link`、`unit/network` | 各层模型、双向属性、状态查询、局部标签与接续 |
| `unit/pc`、`unit/compiler` | 文件恢复、两路编译、字段与 ABI、离线 C 程序行为 |
| `unit/runner` | 调度、计划合并、版本所有权与资源释放 |
| `unit/examples`、`unit/tools` | UT 接入／干扰／计划循环、保存计划视图与执行回放 |
| `unit/environment` | 正式安装来源、混合来源拒绝、实际 wheel 内容与导入、开发脚本 |
| `connections` | 真实相邻模块交接，包括 Scene—SEED、Physical—Link、Link—Network、部署—Compiler |
| `integration` | 真实构建与三层规划、计划保存恢复、BPF／Linux 编译、分段与延迟尾部 |
| `live/runner`、`live/store` | 显式启用的 Docker／内核转发／UT 与 etcd 检查 |

默认测试采用小型真实输入，连接测试写入临时目录，不启动容器。
`SKYSEED_LIVE_IMAGE` 指向已有节点镜像时启用 Runner 现场测试；
`SKYSEED_ETCD` 指向可执行文件时启用 Store 现场测试。
回放 JavaScript 行为测试需要 PATH 中的 Node.js。

先运行修改模块与直接消费者；共享合同变化时再扩大检查。
有效回归应独立判断预期行为，不能只检查实现拼写或无异常。
命令见[开发说明](../docs/development.zh-CN.md)。

S4 InternetMap2 检查覆盖未参与计划的已部署节点、实际接口配对、Zone 分批采用回执、抓包文本映射及直接容器查找／清单共享刷新；UT 回执跟随还覆盖 JSONL 分段写入。
设置 `SKYSEED_TEST_NODE` 为 Node.js 可执行文件可运行 JavaScript 检查。
浏览器检查使用独立的小型测试场景，完整 S4 仍由用户人工验收。
