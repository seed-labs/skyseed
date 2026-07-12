# Stage 3 验收：拓扑、规划和 S4 示例

状态：**PASS**

本门禁未启动 Compose、主机网络修改、BPF 挂载或真实长跑。

## 结构结果

稳定契约/拓扑 IR 已进入 `core/ir/`；可替换拓扑/planner 接口进入 `core/interfaces/`；轨道和地面目录模型进入 `models/`；拓扑 pipeline、shell 传播、实验/Starlink 拓扑进入 `topology/`；planner 验证、routing/G/Z 和 temporal planning 进入 `planning/`。

旧顶层模块名只是临时 alias，不含策略、规划或运行回退；在标准导入切换时删除。此历史阶段由 `examples/s4_operational_clean/scenario.py` 保存显式 S4 选择，后来单一 `experiment.py` 取代它，`scenario.py` 不再是活跃或保留路线。

## Profile 等价

迁移后值匹配 `canonical_profile_snapshot.json`：

| 证据 | SHA-256 | 结果 |
|---|---|---|
| 归一化 profile | `c4efe9d2f319dd37342018ba342a7a3b44e8c7d70a6e21b1a6eacd9f56b52755` | 匹配 |
| 目录选择门禁 | `d1290ba403641488745778c099139c848edcb965a65a75288659c5b1a5f4e084` | 匹配 |
| 底层命令 | `a1e9c4459a50aa4806411d6e899ce8669f660381b5bbadc2f968e385ebb9de82` | 匹配 |

示例独立确认 348 卫星、28 UT、12 GW、14 zone、4×240 秒、10 秒采样、`operational_clean`、严格 `bpf_owned`。

## 有序消费者重放

完整 r139/r140 等价冻结包重放结果：100/100，H0–H3 各 25；四个 prepared horizon；1005 个 consumer binding 引用；零 retired IX 引用；100 个有效 `shared_gz_noop_hold`；零 blocker；172.60 秒。

## 共享 G/Z generation 重放

100/100 generation 由当前代码重放；H0–H3 各 25；使用 schema `skyseed-bpf-owned-compiled-runtime-inputs-v2`；不消费历史 compiled v1 map；零 blocker/map conflict，并覆盖 H3 generation 0 的物理 generation wrap。

## 自动测试

- 新包 13/13；拓扑/profile 101/101；规划/event/runtime-default 352/352；完整旧套件 791/791；
- 编译/导入和 alias 身份检查通过；新包不存在旧平面模块导入；`git diff --check` 通过。

廉价测试发现并修正错误夹具指纹、错误 selector 形状、动态 loader 注册错误和一个与 `tests/contracts` 冲突的旧 `from contracts` 导入；均不需要容器或长跑。

Stage 3 只证明拓扑/规划搬迁和冻结生产者/消费者语义，不声明 runtime/backend 分离或新 CLI 已完成。其余职责留到后续阶段。
