# 按职责组织测试

[English](README.md)

| 位置 | 验证内容 |
|---|---|
| `unit/scene`、`unit/emulator` | 场景身份／几何／持久化、SEED 节点构建与归属 |
| `unit/physical`、`unit/link`、`unit/network` | 各层模型、双向属性、状态查询、局部标签与接续 |
| `unit/pc`、`unit/compiler` | 文件恢复、两路编译、字段与 ABI、离线 C 程序行为 |
| `unit/runner` | 调度、计划合并、版本所有权与资源释放 |
| `unit/examples`、`unit/tools` | UT／干扰／测量、保存计划视图与执行回放 |
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
