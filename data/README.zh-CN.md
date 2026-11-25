# 场景输入

[English](README.md)

| 文件 | 内容 | 使用位置 |
|---|---|---|
| [starlink/shells.yaml](starlink/shells.yaml) | 五组简洁的圆轨道 shell 参数 | [S4 场景构造](../examples/s4_handover/scene.py) |
| [starlink/ground_stations.yaml](starlink/ground_stations.yaml) | 355 个分别记录位置的 GS | 示例先选择站点，再构造节点 |
| [global_zones.yaml](global_zones.yaml) | 732 个分区的显式参考布局 | 可用作自定义输入；`Scene.create_default_zones()` 直接生成同样的分区 |

## S1–S5 参数

| 预设 | 高度 km | 倾角 ° | 轨道数 × 每轨卫星 | RAAN ° | 相位因子 |
|---|---:|---:|---:|---|---:|
| S1 | 550 | 53 | 72 × 22 | 起始 0，间隔 5 | 38.765 |
| S2 | 540 | 53.2 | 72 × 22 | 起始 2.5，间隔 5 | 54.175 |
| S3 | 570 | 70 | 36 × 20 | 起始 0，间隔 10 | 15.295 |
| S4 | 560 | 97.6 | 6 × 58 | 起始 63.7，间隔 60 | 1 |
| S5 | 560 | 97.6 | 4 × 43 | 75.7、87.7、99.7、117.7 | 2/15 |

规模、高度、倾角、轨道方向和 2015-01-01 UTC 历元来自已核对的
[FCC 记录](../research/starlink/gen1/shells/fcc-sat-mod-20200417-00037-gen1.json)，
[映射说明](../research/starlink/gen1/shells/S1-S5-MAPPING.md)标明来源轨道。
S4 保留原始轨道编号 5–10；编号不影响几何。

相位因子是**从记录归纳的生成参数**，不是 FCC 另行公布的 Walker 因子。
生成角度与来源中保留一位小数的相位相差不超过 0.05°，计算保留完整精度。
首颗卫星相位默认零，S5 为 0.29°。
[Scene 说明](../src/skyseed/scene/README.zh-CN.md)给出公式与接口。
6 × 59、6 × 60 等都是合法的自定义布局。参数文件不再逐星列记录，也不包含
链路容量假设；连接数量预算属于 Link 模型。

示例读取参数后调用 `Scene.create_shell`，Scene 本身不解析此 YAML。
保存的 `.scene` 仍包含生成好的节点、分组和几何，消费者直接读取，无需再次生成。
原始研究资料单独保留，不参与运行时加载。

## 地面站来源

GS 坐标来自已核对的[站点记录](../research/starlink/gen1/ground-stations/gateway-evidence-2026-08-25.json)
与[来源说明](../research/starlink/gen1/ground-stations/PROVENANCE.md)。清单包含历史和规划站点，
不代表当前全部在运营，也不说明硬件容量。在仓库根目录核对：

```bash
python data/starlink/rebuild_frozen_ground_stations.py --check --research-source research/starlink/gen1/ground-stations/gateway-evidence-2026-08-25.json
```

`--write` 显式重建该文件；正常示例只读取资料。
