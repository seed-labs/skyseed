# Skyseed

Skyseed is a composable satellite Internet emulation platform built as a major
extension of the [SEED Emulator](https://github.com/seed-labs/seed-emulator).
It separates logical network description and planning from backend compilation
and online runtime updates, so researchers can construct satellite networks,
replace models and algorithms, run experiments, and audit their results
without rewriting the platform core.

Skyseed v1 is the first behavior-validated release. It provides complete S4
and S5 examples, Linux and BPF backends, rolling-horizon runtime updates,
reusable orbit and ground inputs, visualization interchange, audit evidence,
and tiered tests. Historical pre-v1 implementation is retained in Git history;
the current source of truth is `skyseed_v1/`.

## Quick start

```bash
python -m pip install -e ../seed-emulator-upstream-dev
python -m pip install -e skyseed_v1

python skyseed_v1/examples/s5_operational_clean/run.py inspect
python skyseed_v1/examples/s5_operational_clean/run.py preflight
python skyseed_v1/tests/run.py
```

A real experiment starts directly from its composition file:

```bash
python skyseed_v1/examples/s5_operational_clean/run.py
```

Read the [English project guide](skyseed_v1/README_EN.md) or
[中文项目指南](skyseed_v1/README_ZH.md). Architecture and environment details are
available in [English](skyseed_v1/ARCHITECTURE_EN.md) / [中文](skyseed_v1/ARCHITECTURE_ZH.md)
and [English operations guide](skyseed_v1/docs/operations/environment_and_running_EN.md) /
[中文运行指南](skyseed_v1/docs/operations/environment_and_running_ZH.md).

---

Skyseed 是一个基于 SEED Emulator 大幅扩展而来的可组合卫星互联网仿真平台。
项目把逻辑网络描述与具体后端实现、在线动态更新分离，使研究者能够构建自己的
卫星网络、替换模型和算法、组织实验并审计结果，而不必修改平台核心。

Skyseed v1 是首个经过行为验证的版本，当前仓库的标准实现全部位于
`skyseed_v1/`。早期实现仍保留在 Git 历史中，但不再作为第二条可执行路线。
