# S4 手动实验

[English](README.md) · [主流程](../README.zh-CN.md)

本目录保存 S4 示例的临时 UT 接入、区域中断模型、计划循环、控制台和离线查看导航。
部署后由用户自行启动业务和抓包。ping、nc 与接口观察见[操作手册](../MANUAL.zh-CN.md)。

## 默认配置

| 项目 | 设置 |
|---|---|
| 星座 | S4，6 轨 × 58 星，高度 560 km、倾角 97.6° |
| GS | 按地理选择 32 个：北美 5、南美 5、欧洲 9、非洲 1、亚洲 7、大洋洲 5 |
| 来源 | `data/starlink/shells.yaml`、`ground_stations.yaml`；GS 冻结于 2026-08-25，包含历史／计划条目 |
| Zone | 732 个全球分区 |
| 时间 | 600 秒，每 60 秒保存一批；Link／Route 每 5 秒评估 |
| 链路 | 默认准备时间 5 秒 |
| 路由 | GZ 的本地 MPLS-like 标签转发，无额外 Delivery 延迟 |
| UT | 杭州 (30.2741°N, 120.1551°E)，`zone-b16-c033`，业务地址 `192.0.2.2` |
| 关联 GS | 按地理优先顺序为 Yamaguchi、Akita、Hitachinaka |
| 中断 | Yamaguchi 圆形区域 (34.2171°N, 131.55566°E, 180 km)，西欧圆形区域 (50.2°N, 1.5°E, 600 km)，时间 180–240 秒 |

按地理选取 GS，将实验规模控制在 32 个站点。

## 接入与换星

UT 根据位置选择所属 Zone。服务星使用该 Zone 中心 (33.75°N, 117°E) 选择：
仰角至少 5°，取最高者。GS 接入还要求可见、链路选择和准备完成。
三个关联 GS 按 Zone 中心的球面距离选择；应用自行选择 GS 地址。

[UT 适配](ut/README.zh-CN.md)跟随实际采用的 Zone 出口规则。
换星时重建接入 veth，保留 UT 容器和原始 IP 报文。
GS 的入口分类将发往 UT 的回复接到 Zone 原有标签路径。
由用户自行启动应用和抓包。

## 循环与控制台

`repetition.py` 将保存的 BPF 计划向前平移，只恢复终点与起点状态的差异，保留容器和地址。
每 600 秒恢复起点拓扑。
在 `run.py` 中设置 `REPEAT = False` 可只执行一个区间。

`console.py` 显示阶段、轮内时间和当前 UT 服务星。Ctrl+C 调用 `Runner.stop()`，
随后由各自上下文清理 UT、Runner 和部署资源。运行状态及采用／接入记录仍可查询。

## 区域中断模型

`perturbation.py` 定义 `RegionalGroundOutage`，持有中断区间、圆形区域和跨批次状态。
`simu.py` 注册方式：

```python
link.set_perturbation_hook(RegionalGroundOutage)
```

使用 `link.set_perturbation_hook(None)` 可关闭。基础链路和 Network 路由输入不变，
仅中断执行中的 GS 链路，ISL 和 UT 接入不受此模型影响。
恢复时采用当前基础计划中仍存在的连接及属性。区域成员按球面距离判定。

`configuration.py` 保存实验设置和计划换星时刻。
`view.py` 使用既有观察 Tools 查看保存的 Scene、Physical、Link、Network 和 Compiler 数据。
