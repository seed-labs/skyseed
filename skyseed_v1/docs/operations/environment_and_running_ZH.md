# Skyseed v1 环境与运行

这是独立 Skyseed v1 检出的标准安装和运行指南，不依赖已退役迁移树。最新廉价测试、真实运行和重启证据见 `validation_status_ZH.md`；迁移验收文件是历史快照，不是当前状态页。

## 两级环境

廉价开发只需要 Python 和 SEED Emulator，可运行导入/契约测试、`run.py inspect`、命令/profile preview，以及不请求具体 Docker 后端的拓扑、规划和可视化测试。

真实 operational-clean 还需要 Linux 主机参与：运行中的 Docker/Compose；`ip`、`tc`、namespace、bridge、sysctl；支持 BPF target 的 Clang 和可用 `bpftool`；root 或非交互 `sudo -n`；以及与示例规模相称的磁盘、内存、容器、子网和接口容量。真实要求由 `preflight` 检查，不施加给普通单元测试。

## 已验证环境身份

| 组件 | 已验证身份 |
|---|---|
| Python | CPython 3.10.20（`seed310`） |
| SEED Emulator | 官方 `seed-labs/seed-emulator`，`master`；迁移开始 commit `61acdf4c9699b25b06843ec4aad747108b05924e` |
| PyYAML / requests / sgp4 | 6.0.2 / 2.22.0 / 2.25 |
| Docker Engine / Compose | 19.03.8 / `docker-compose` 1.27.4 |
| Clang | 10.0.0，支持 BPF target |
| 标准示例 bpftool | `/usr/lib/linux-tools/5.4.0-216-generic/bpftool`，v5.4.291 |
| 主机 | Linux x86-64，支持 namespace、veth/bridge、qdisc/netem、tc-BPF、bpffs |

这些版本记录成功环境，不表示所有新版本不兼容。Kernel、Docker、SEED、Clang 或 bpftool 变化后，真实运行前必须通过 preflight 和相关离线/原生门禁。

## 检出和 Python 安装

使用 Python 3.10。在含 `pyproject.toml`、`examples/`、`src/` 的 v1 目录中：

```bash
python3.10 -m venv .venv
source .venv/bin/activate

git clone --branch master https://github.com/seed-labs/seed-emulator.git \
  ../seed-emulator-upstream-dev

export SKYSEED_SEEDEMU_ROOT="$(cd ../seed-emulator-upstream-dev && pwd)"
unset PYTHONPATH

python -m pip install --upgrade pip
python -m pip install -e "$SKYSEED_SEEDEMU_ROOT"
python -m pip install -e .
```

当前迁移工作区从 Git 根运行最后一条等价命令为 `python -m pip install -e skyseed_v1`。当 SEED 位于同级标准目录时 `SKYSEED_SEEDEMU_ROOT` 可省略；显式设置便于审计。不要把过时 `/home/seed/seed-emulator` 放入 `PYTHONPATH`。

验证导入：

```bash
python - <<'PY'
import seedemu
import skyseed_v1
print("skyseed_v1:", skyseed_v1.__file__)
print("seedemu:", next(iter(seedemu.__path__)))
PY
```

## 真实运行的主机工具

发行版包名可不同，但主机需提供 Docker/兼容 Compose、iproute2 的 `ip`/`tc`、带 BPF backend 的 clang/llvm、可用内核工具集的 bpftool、sudo/sysctl/nsenter。Docker 必须运行且当前账户可用。后端在非 root 时使用 `sudo -n`，交互密码不是受支持路径；权限应按机器实际范围配置，本文不建议普遍授予无限 sudo。

标准 profile 目前绑定明确 bpftool 路径。换主机前确认 effective profile 中路径存在、可执行且版本可用。缺工具、过时 SEED 覆盖、旧归属状态、bridge-netfilter 不兼容或磁盘不足会由 preflight 失败/告警。

## 廉价验证序列

在 v1 目录：

```bash
python tests/run.py
python examples/s4_operational_clean/run.py inspect
python examples/s5_operational_clean/run.py inspect
python examples/s5_operational_clean/run.py preflight
```

从迁移工作区根运行时加 `skyseed_v1/` 前缀。

- `inspect`：解析 Python 组合并验证计数、间隔、资格窗口和派生 profile；
- `preflight`：不启动实验，只检查主机和归属残留；默认短摘要，`--format json` 输出完整审计；
- `clean`：只扫描/删除 Skyseed label/name、固定 BPF pin、直接接口模式和运行锁；活跃进程存在时拒绝，最后执行同范围 post-clean 判断；
- `preview`：解析标准底层命令并写 effective profile/容量证据，不创建网络；
- 无 action：执行真实准备、Compose、主机/BPF runtime、观测、审计和清理。

真实入口立即输出 run ID、路径、规模、初始阶段和 Ctrl-C 语义。首个持久快照前有低频预览心跳，之后由持久证据投影阶段和 epoch；不变阶段显示耗时、活动子操作和可用 Compose 状态。该 daemon 观察者输出合并且有界，不参与调度、锁或更新完成。

`ready=false` 或 blocker 必须停止。低磁盘等 warning 即便 ready 也需审查。完整 S4/S5 应保留充足空间，四 horizon prepared 证据本身即可超过 1 GB。直接示例会自动移除已知退役 SEED `PYTHONPATH` 值并选择配置的当前检出，用户不需要先 `unset PYTHONPATH`；其他自定义项保留。

## 运行示例

```bash
python examples/s4_operational_clean/run.py
python examples/s5_operational_clean/run.py
```

示例只写自己的忽略 `output/<run-id>/`；项目还使用忽略 `.runtime/` 保存全局 active lock、`.cache/` 保存可复用原生对象。正常或失败清理必须使容器、网络、接口、BPF pin、锁、worker 为零。只有清理完成且证据不再需要后才删除 `output/`。

## 重启后或现场演示前

运行中不要重启。若 VM 稳定且无系统更新要求，现场前保留已验证 boot 比临时重启风险更低；必须验证重启时应留出清理和排练时间。

重启后从仓库根：

```bash
systemctl is-active docker
findmnt -n -o FSTYPE /sys/fs/bpf
/home/seed/miniconda3/envs/seed310/bin/python \
  -m skyseed_v1.operations.prepare_host_mpls --format json
python skyseed_v1/examples/s5_operational_clean/run.py clean
python skyseed_v1/examples/s5_operational_clean/run.py preflight
python skyseed_v1/examples/s5_operational_clean/run.py preview
```

期望 Docker 为 `active`、文件系统类型为 `bpf`、host prepare 成功、范围清理成功、preflight `ready=True` 无 blocker，preview 解析正确 S5 且不建网。当前已验证硬重启后 bridge netfilter sysctl 恢复为 1，标准 `prepare_host_mpls` 将其恢复到运行所需状态并准备 MPLS；这属于标准准备，不应由人工逐项修改。

若 bpffs 缺失，不要开始演示，可在当前 Ubuntu 主机显式挂载后重复检查：

```bash
sudo mount -t bpf bpf /sys/fs/bpf
```

不要在演示前升级 package、kernel、Docker、Compose、SEED 或 bpftool。

## 当前时长边界

- S4 地面预设由已验收 960 秒运行验证，增加 4×240 horizon 会在资格扩展前配置失败；
- S5 的 9 UT/5 GW/6 zone 预设从 `2026-07-09T00:00:00Z` 起验证 7200 秒，240 秒 horizon 下最多 30 个；两小时是分析窗口，不是周期声明；
- `continuous=True` 目前仍是有限 `loop_count`，未实现一直 replenish 到 Ctrl-C；Ctrl-C 是必须进入清理的提前中断。

更换开始时间或超过资格时长必须在建网前失败，应重建或定义合适的 shell 专属预设，不能禁用门禁。

## 运行之后

再次 preflight，并保留运行目录直到结果和清理证据审查完：

```bash
python examples/s5_operational_clean/run.py preflight
```

昂贵运行证据和重跑策略见 `expensive_runs_ZH.md`。

## AI/操作者交接协议

新 Codex 或维护者换主机时先读本文，不要在测量环境差异前编辑 profile；后续设计还应读 `../architecture/design_backlog_ZH.md`。

先收集临时证据：

```bash
mkdir -p /tmp/skyseed-environment-audit
unset PYTHONPATH
python -m skyseed_v1.operations.kernel_probe \
  --json-out /tmp/skyseed-environment-audit/kernel.json
python examples/s5_operational_clean/run.py inspect \
  > /tmp/skyseed-environment-audit/s5-inspect.json
python examples/s5_operational_clean/run.py preflight --format json \
  > /tmp/skyseed-environment-audit/preflight.json
python tests/run.py \
  > /tmp/skyseed-environment-audit/tests.stdout 2>&1
```

`kernel_probe` 在有权限时创建并删除临时 namespace/veth/qdisc，只能在无活跃实验时运行。Preflight 非零时保留 JSON 并诊断 blocker，自动验收不得追加 `|| true`。

接收方应报告：Skyseed/SEED commit；Python 和解析路径；OS/kernel/架构/cgroup/Docker/Compose；Clang BPF、bpftool、BTF、bpffs；ip/tc、namespace、netem、bridge-netfilter、MPLS、非交互权限；磁盘/内存和残留；各廉价门禁状态。

不要复制 output、cache、lock、venv、Docker 状态、BPF pin 或旧绝对路径。只复制已提交源码/数据，重新安装依赖，让示例生成自己的 `output/`。

## Ubuntu 20.04 到 24.04 风险审计示例

| 可能变化 | 风险 | 真实运行前最小证据 |
|---|---|---|
| Python 高于 3.10 | 导入成功不证明依赖/序列化等价 | 先用 3.10；否则完整测试并明确验证新解释器 |
| Compose v2 | 解释、项目/网络命名、就绪、清理可能不同 | preview、审查 Compose、有界 create/down/零残留 |
| cgroup v2 | 资源、权限、namespace 行为变化 | preflight + 有界容器/就绪/清理 |
| 新 kernel/BTF/verifier | map、helper、tc attach、pin 变化 | kernel probe、冷编译、loader/map ABI、attach/detach/零 pin |
| 新 Clang/LLVM | BPF codegen/flag 变化 | 编译两份原生源码并比较 map 契约 |
| bpftool 路径变化 | 严格 profile 要求显式工具 | 显式更新环境/profile，运行 feature/map/cleanup probe，禁止静默 fallback |
| 新 iproute2/tc/netem | batch、输出、删除变化 | namespace/veth/netem 和 qdisc apply/rollback |
| nftables/bridge sysctl | 数据包主机过滤路径变化 | 检查 bridge-netfilter 并保留 passthrough 语义 |
| AppArmor/sudo/Docker 权限 | 后期阻断或交互等待 | 准备前证明 `sudo -n`/root 和 Docker |
| 新 SEED master | layer/compiler/container 输出可合法改变 | 记录 commit、adapter/topology 测试、Compose diff、有界 SEED compile |

Python/库漂移先做 import/unit/contract/integration；planner/model/data 漂移还需有序离线 replay；Docker/kernel/BPF/tc/namespace/权限/SEED compiler 漂移在长跑前必须有有界 system gate。失败应在归属层修复，禁止通过禁用 preflight、削弱清理、改变转发归属或增加旧 fallback 让新主机“通过”。
