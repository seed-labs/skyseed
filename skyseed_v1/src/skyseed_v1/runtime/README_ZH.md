# 运行时

负责 epoch 顺序、horizon 准备、激活、worker 交接、退役和回滚编排。网络激活的优先级高于观测、控制台输出和产物展示。

当前大型模块只通过分阶段、保持契约的抽取来拆分，参见 `docs/architecture/directory_growth_ZH.md`。
