# Skyseed v1

本目录是完成纯结构重构后的 Satellite Internet 1.0 标准项目。已验证行为仍锚定 Git tag `satellite-internet-v1.0-baseline`；只有生产者、消费者、产物和失败契约全部通过迁移门禁后，代码才会在这里成为标准实现。

Skyseed 的目标是成为可组合的卫星互联网仿真平台。用户应能描述逻辑卫星网络、选择可替换的模型与规划器、把描述编译到具体后端，并在不修改平台核心的情况下运行或审计实验。

可复用场景数据和 Python 实验组合位于 `data/`、`examples/`，解析后的 profile 是生成的运行产物。迁移工作区可能仍有同级旧树用于历史回归比较，但 v1 不导入、不读写也不依赖它；只包含本项目的检出仍是受支持形态。

Python/SEED 安装、Linux/Docker/BPF 要求、主机迁移、预检、真实运行、输出归属和当前时长边界见 `docs/operations/environment_and_running_ZH.md`。

## 标准入口

开发时以 editable 方式安装项目，然后运行或复制示例目录。Python 文件是面向人类的主要组合根：

```bash
/home/seed/miniconda3/envs/seed310/bin/pip install -e skyseed_v1
python skyseed_v1/examples/s4_operational_clean/run.py inspect
python skyseed_v1/examples/s4_operational_clean/run.py preview
python skyseed_v1/examples/s4_operational_clean/run.py
```

最后一条命令启动真实实验，并把生成输入、有效 profile 和证据写到示例自己的 `output/`。入口会立即输出已接受启动、输出路径、实验规模和初始阶段；第一个持久快照准备期间会有有界心跳，之后的阶段变化和 epoch 完成数来自该快照。长期不变的阶段也会输出耗时和可用阶段细节。控制台投影运行在独立 daemon 线程，绝不参与网络调度或更新完成判定。详细证据始终保留在运行目录中。

供自动化使用的长参数 CLI 仍保留：

```bash
skyseed-v1 list-examples
skyseed-v1 run --example s4_operational_clean --dry-run --progress off
```

完整 S5 示例使用相同地面/运行时策略但卫星更少，操作方式相同：

```bash
python skyseed_v1/examples/s5_operational_clean/run.py inspect
python skyseed_v1/examples/s5_operational_clean/run.py
```

`cli/` 只解析和转发选择；实验编排属于 `runtime/`，拓扑、规划、编译和后端行为保留在各自模块。

## 廉价检查

从仓库根目录运行：

```bash
python skyseed_v1/tests/run.py
```

`unittest discover` 是等价的自动化接口。v1 之前的回归套件保留在 Git 历史和
全量备份仓库中，不再作为当前检出的第二套活跃测试或实现树。

依赖规则见 `ARCHITECTURE_ZH.md`，分阶段切换计划见 `MIGRATION_ZH.md`。
