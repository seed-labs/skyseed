# Skyseed v2 文档入口

路径就是文档身份；front matter 或 manifest 只能补充检查，不能改变以下
分类。

| 路径 | 主要读者 | 权威性 | 默认实现输入 |
|---|---|---|---|
| [`accepted-design.md`](accepted-design.md) | 架构审核者 | 最高架构锚点 | 是 |
| [`CURRENT.md`](CURRENT.md) | 全体维护者 | 唯一当前状态来源 | 是 |
| [`human-readable/`](human-readable/) | 人工审核者 | 当前架构图 | 是 |
| [`codex-reference/`](codex-reference/) | Codex / 开发维护者 | 当前详细合同 | 是，按任务局部读取 |
| [`user-guides/`](user-guides/) | 使用者 | 当前操作指南 | 是 |
| [`candidates/`](candidates/) | 设计讨论者 | 非权威候选 | 否 |
| [`provenance/`](provenance/) | 调查/复现人员 | 非权威历史事实 | 否 |

正常实现任务从代码、tests、public exports、`accepted-design.md`、
`CURRENT.md` 和相关的 current 文档开始。`candidates/` 与 `provenance/`
只在任务明确需要候选设计或历史调查时读取。

图的唯一维护位置：

- 系统总图：`human-readable/system-overview.md`；
- Emulator owner/创建链：`human-readable/emulator-chain.md`；
- Simulator / Topology / Slicer / Models 链：
  `human-readable/simulator-chain.md`；
- 正式对象、owner、cardinality 与创建关系：
  `human-readable/object-model.md`；
- public class 的完整局部 member 与 role stereotype：仅在对应
  `src/skyseed_v2/**/README.md` owner 文档；
- 术语导航：`human-readable/terminology.md`；
- package/import map：`human-readable/repository-map.md`；
- examples/Tools/tests 对应关系：`human-readable/traceability-map.md`；
- `src` owner README 维护该 package 的唯一详细类图；Tools README 只说明
  局部入口、责任和同步目标。它们都不复制全局对象图。

文档检查不扫描后猜身份。`scripts/check_documentation.py` 按这些路径执行：

```bash
python scripts/check_documentation.py --scope focused
python scripts/check_documentation.py --scope full
```

Focused 检查权威锚点、详细合同和 owner README。Full 额外检查全部 current
文档、examples、Tools 与根文档；candidates/provenance 只做基础 Markdown
和本地链接检查。该脚本的 Mermaid 检查只是结构预检；视觉渲染门禁必须
另用固定版本 `mermaid.min.js` 执行。以下临时下载不写入仓库；脚本同时校验
版本标签和 SHA-256，因此 CDN 内容漂移会直接失败：

```bash
mermaid_tmp_root="$(mktemp -d)"
curl -fsSL \
  https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js \
  -o "${mermaid_tmp_root}/mermaid.min.js"
python scripts/check_mermaid_render.py --scope full \
  --mermaid-js "${mermaid_tmp_root}/mermaid.min.js" \
  --mermaid-version 10.9.1
```

固定文件 SHA-256 为
`61b335a46df05a7ce1c98378f60e5f3e77a7fb608a1056997e8a649304a936d6`。
