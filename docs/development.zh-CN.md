# 开发说明

[English](development.md)

复用现有 `skyseed_dev` 环境，需要新环境时按[安装说明](../README.zh-CN.md#安装)配置。
`pyproject.toml` 只从 `src` 发现包，默认收集 `tests`。
示例正常导入安装的 `skyseed`，不自行修改 Python 搜索路径。

## 开发脚本与结果工具

`scripts/` 用于安装和检查仓库；运行仿真不需要逐个执行这些脚本。
`tools/` 则提供镜像构建、场景／计划／执行记录查看等可选功能，供示例和用户调用。

| `scripts/` 文件 | 用途 |
|---|---|
| `setup_dev_environment.sh` | 安装开发环境与相邻 SEED checkout |
| `check_dev_environment.py`、`check_active_source.py` | 核对解释器、实际导入路径和 SEED 能力；后者也供测试调用 |
| `check_import_dependencies.py` | 检查 Scene 与离线 P&C 的依赖边界 |
| `check_documentation.py` | 检查当前 Markdown 的本地链接、锚点和代码围栏 |
| `check_mermaid_render.py` | 用指定 Mermaid 文件和 Firefox 实际渲染文档中的图 |
| `_webdriver.py` | 渲染脚本内部使用的浏览器辅助函数 |

## 按改动选择检查

| 改动 | 检查入口 |
|---|---|
| 安装或包路径 | `python scripts/check_dev_environment.py` |
| 模块依赖 | `python scripts/check_import_dependencies.py` |
| 模块行为 | `python -m pytest tests/unit/<module>` |
| 相邻模块交接 | `python -m pytest tests/connections` |
| 完整离线流程 | `python -m pytest tests/integration` |
| 包内容与 wheel 导入 | `python -m pytest tests/unit/environment/test_packaging.py` |
| 当前文档链接 | `python scripts/check_documentation.py` |

先检查受影响模块及直接消费者。默认套件离线，[现场测试](../tests/README.md)需显式设置环境。
目录或打包切换应检查当前离线套件与实际 wheel；文档或展示修改不要求重跑长实验。
Compiler 与 wheel 检查需要[运行环境](usage.zh-CN.md#运行环境)中的 clang／Linux 头文件，
但不会启动 Docker。`PATH` 中有 Node.js 时运行 JavaScript 回放检查，否则跳过；
安装在其他位置时可用 `SKYSEED_TEST_NODE` 指定可执行文件。
链接检查覆盖维护中的 Markdown 说明；HTML 模板在生成后的产物中检查，历史研究笔记不作为发布指南。

操作留在真实 owner，命名明确，控制流易读。Scene 保存事实，Model 保存算法与配置，
Compiler 产生稳定后端指令，Runner 管理活资源。
不增加兼容回退、重复 manager 或对同一固定输入的逐层检查。
使用小而真实的输入，回归测试检验行为而非实现拼写，关注并报告昂贵工作的耗时。

模块 README 维护接口和关键输出字段，中英版本同步；指南链接合同而不重复抄写。
先用图说明职责或流程，用表说明关键数据，再用短例子解释调用；详细边界按需展开。
示例展示必要调用，实验专用设施明确区分。

## 分支协作与发布

| 分支 | 用途 | 成果去向 |
|---|---|---|
| `feature/ut` | UT 功能开发 | PR → `development` |
| `feature/perturbation` | Link Perturbation 功能开发 | PR → `development` |
| `development` | 核心开发、集成与验收 | PR → `main` |
| `main` | 正式发布 | 保留稳定版本 |

发布后继续使用 `development`。这类长期分支建议通过 merge commit 合入 `main`，
保留共同历史；随后将 `origin/main` 快进合回本地 `development`（无分叉时），再推送同步。
PR 的 Approve 与 Merge 是两件事：作者不能批准自己的 PR，有合并权限且满足仓库规则时可以自行合并。

核心接口更新后，专项开发者先保存本地工作，再在自己的分支合入 `origin/development`：

```bash
git fetch origin development
git switch feature/ut
git merge origin/development
```

`feature/perturbation` 同理。存在冲突时按新版接口调整，不把退役目录重新带回；
共享分支使用普通合并，保留他人的提交。功能完成后向 `development` 提 PR。

研究改动与代码发布分开，本地协作记录不属于安装后的 API。
`setup.py` 在构建前移除过期包文件，整理模块时保留该能力。
