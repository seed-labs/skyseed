# Stage 7 验收：标准切换

状态：**PASS — 已接受标准结构切换**

由新鲜输入构建的四 horizon 真实运行成功。Satellite Internet 1.0 现在只有 `skyseed_v1/` 一个实现根，旧 runner 路线已退役。后续性能和入口 UX 仍需廉价回归门禁及用户真实运行。

## 已接受运行

- run ID：`s4-v1-structural-cutover-fresh2-20260712T124412Z`；
- 目录：`/home/seed/skyseed_runs/validation_profiles/s4-v1-structural-cutover-fresh2-20260712T124412Z`；
- 约 1 小时 41 分 45 秒；返回 0；结果 `RICH_S4_OPERATIONAL_CLEAN_PASS`；
- 最终容器、Compose 网络、直接接口、BPF pin、锁和进程残留为零。

命令：

```bash
env -u PYTHONPATH skyseed-v1 run \
  --example s4_operational_clean \
  --run-root /home/seed/skyseed_runs/validation_profiles \
  --run-id s4-v1-structural-cutover-fresh2-20260712T124412Z \
  --format json
```

这是新鲜正常准备，不是加载 r139 包。一次旧 frozen 输入尝试因路径迁移改变绑定身份而正确失败关闭；首次新鲜尝试在 Compose 前暴露迁移后的局部变量错误，修复并回归后才执行验收运行。

## 运行等价证据

100/100 BPF-owned epoch，H0–H3 各 25；全部事务成功；三个 successor worker ready 并正确消费；75/75 inactive generation，99 个 steady transition 使用预加载 flip-only；无 full attach/reload、Linux G/Z fallback、classifier miss blocker、运行失败或隐藏修复；回滚、Compose down、postflight 均成功。

完成后派生正常路径审计因读取压缩前路径产生三条假 blocker；不可变证据完整。Reader 修正为读取当前 externalized queue、rich timing readiness、顶层 cleanup scope、独立 coverage 和 worker-ready 产物，并在任何 `*_BLOCKER` 存在时失败关闭。由不可变源重生成后 blocker 为零。

## 时间与成本

| 阶段 | 时间 |
|---|---:|
| preview | 129.51 s |
| frozen horizon 准备 | 646.16 s |
| normal input 构建 | 141.96 s |
| externalization | 93.67 s |
| Compose build | 1355.36 s |
| Compose up | 430.74 s |
| host prewarm | 466.53 s |
| pre-runtime BPF | 495.07 s |
| runtime cleanup | 288.38 s |
| Compose down | 442.61 s |
| postflight | 6.83 s |

100 epoch steady mean 7.55 秒、P95 15.13、最大 20.18；平均比 r140 改善约 3.3%，尾部退化约 10.8%/16.3%。activation lag 平均 25.57、最大 53.79，显著优于 r140；旧 `deadline_miss` 在 strict deadline 关闭时仍按零延迟定义报告全部 epoch。

审计发现 base epoch record 在 `transition_wall_seconds` 之后同步构建，累计约 105 秒且被误标为非阻塞；未调度验证时还每 epoch 两次读取全局 BPF counter。验收后代码跳过无验证时的成对读取、移除重复 rich readiness 遍历，并在真正验证时记录读计时；均有回归测试，但未因此再由助手长跑。

## 切换门禁

新分层套件 49/49；迁移套件在删除旧隐式 profile 路径测试后 791/791；编译和 diff check 通过；旧 runner/alias 已不存在；包源码无旧实现导入；worker 以模块启动；S4 profile/数据归属新位置且保持内容契约；SEED 为官方 master commit `61acdf...`；消费者和 generation 重放均 100/100；原生 BPF 冷编译及 v17 契约通过；postflight clean。低磁盘只是在下次昂贵运行前处理的告警，不是运行残留。

主要人类入口为 `python skyseed_v1/examples/s4_operational_clean/run.py`，同文件提供 `inspect`、`preflight`、`preview`；长参数 CLI 继续供自动化使用。
