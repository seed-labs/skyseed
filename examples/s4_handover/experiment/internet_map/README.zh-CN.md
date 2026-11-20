# S4 InternetMap2 适配

[English](README.md) · [操作手册](../../MANUAL.zh-CN.md)

`python map.py` 将已安装 SEED 仓库的 `tools/InternetMap2` 复制到
`output/internet-map2`，加入本例适配并构建 `skyseed-s4-internet-map` 镜像。
`run.py` 随网络启动地图，退出时停止。SEED 仓库作为上游源码，生成副本保存在 output。
沿用 InternetMap2 原有的 Node 22／pnpm／Vite 构建方式，网页为
[InternetMap2](http://localhost:8080/pro/map)。

沿用原图、信息面板、终端窗口、Filter 和 Log。适配按 Scene 生成固定区域布局，每两秒原地更新
节点与连线。双击节点进入原终端，选择连线查看两端。
卫星短编号位于圆点内，轨道分组名位于环中央；搜索与流量高亮保持节点大小。
Filter 输入 `not ip6` 可避开 IPv6 背景流量；`all` 抓取全部流量。
原 Log 保留抓包文本，包括 MPLS label；闪烁根据每条抓包记录定位到容器及接口。
清空 Filter 后 Submit 停止抓包。

普通连线随节点选择显示，提供“显示全部连接”开关。UT 接入及已关注 Zone 的服务关系
保持显示；抓包独立于节点选择，对应的实际链路自动显现并高亮 2.5 秒。
GS／卫星详情提供 Zone 选择框；只有关注的 Zone 加入图中，在卫星环下方使用固定槽位，
浏览器本地存储保留关注项和位置；每次打开页面会补上当前 UT Zone，进入后仍可取消关注。
Zone 详情复用原面板、终端入口，并提供填好 IP 的
ping 命令供复制。紫色虚线服务关系与实际流量链路分开显示。

| 显示信息 | 来源 |
|---|---|
| 分组、固定位置、容器身份 | Scene 和已部署容器的 Scene 身份标签 |
| 身份 IP | 有对应编译目标时读取；全部接口地址来自实际接口查询 |
| 容器状态 | 对本次运行的已知容器直接执行 Docker inspect |
| 接口、IP、连接两端 | 只读查询容器网络空间的 netlink；核对 veth 两端编号 |
| UT 服务星 | 接入状态，结合实际接入接口核对 |
| Zone IP、位置、关联 GS | 编译后的端点目的地址、Scene、已保存的 build 关联结果 |
| 负责 Zone | 最终交付的采用回执，同时核对运行中卫星的 ssidentity 地址 |
| 链路时延／丢包 | 已采用的链路指令 |
| 卫星位置 | 轮内时间之前最近的已保存 ECEF 位置采样，同时显示采样时刻 |

地图显示全部已部署的 Scene 节点，包括已保存计划中没有操作的 GS。

## 文件职责与替换

| 文件 | 职责 |
|---|---|
| `layout.py` | 按轨道数、每轨节点数和 GS 数生成卫星环、UT、GS、Zone 区域；只决定显示位置 |
| `temporary_ut.py` | 将本例 `terminal.json`、`attachment-state.json`、单 UT／`ssut` 约定接到地图 |
| `runtime.py` | 汇集 Scene、部署身份、编译地址与回执索引，管理地图容器 |
| `collector.py` | 只读采集实际接口／veth 对端，增量读取采用回执，更新 Zone 服务和地图状态 |
| `frontend.mjs` | 更新 SEED 图、关注 Zone、详情与终端入口 |
| `capture.mjs` | 将 SEED 抓包定位到接口／边，复用其录制事件并保存 JSONL |
| `backend.js` | 本轮容器清单、终端 ID 解析、状态 API 和观察进程 |
| `build.py` | 复制上游、接入必要补丁、摘录位置数据并构建镜像 |

环半径按每轨节点数确定，环的行列按轨道数确定；GS 位于环区右侧，UT 在左侧，
Zone 区位于卫星和 GS 区下方。关注槽位不随换星或其他 Zone 的增删而移动，节点不能拖动。
布局间距在 `layout.py` 集中调整，无需为 S5 复制一份页面。

临时 UT 的网络实现仍在相邻的 `ut/`。正式 UT 系统可替换它及 `temporary_ut.py`，
再按自身终端数量／位置／状态调整地图展示；这里的单 UT 文件格式不是正式系统的接入要求。
图还依赖本例 BPF 标签采用回执、`ssidentity` 和默认 GZ 关联，并非任意路由模型的通用视图。
AS150、8080 端口、项目和镜像名称也是本例设置，换 shell 不要求改这些名称。

全部可视化实现集中在本目录；外部只有 `map.py` 构建入口及 `run.py` 的 import／with 接入。
以后替换正式可视化时可一并移除这些入口、本目录及对应测试／说明，清理生成的
`output/internet-map2`、地图状态／位置摘录和镜像。UT、核心包和 SEED 源码无需随之回退。
适配只修改上游的生成副本，使用根 README 推荐的 SEED 版本即可，不需要第二仓库补丁；
上游页面结构变化时，`build.py` 会指出失效的接入位置。

## 手动流量记录

Log 内 **Record → Stop recording → Save** 复用 SEED 的页面内存事件表，保存当前录制的
JSONL。每条保留原始包文本（含 tcpdump 时间与 MPLS label）、完整节点名、容器、接收时间，
以及最近地图状态中的接口、对端和轮内时间。过滤器决定实际抓到什么；录制不会发包。
页面刷新、Log 的 clear 或开始新录制会清掉旧记录，先 Save 即可保留到浏览器下载目录。
这份记录便于对照多点抓包；接口映射带有状态采样时刻，不是完整的历史拓扑回放或自动 TCP 测量。

容器清单每两秒共享刷新，最多同时 inspect 十二个本轮容器；终端直接解析已知容器 ID，
避免终端与抓包反复调用 Docker 全局清单。

地图容器通过 Docker socket 使用 SEED 终端和抓包，通过只读宿主挂载中的已有
Python 环境读取网络空间。网页绑定 `127.0.0.1:8080`，容器带有
`skyseed.example=s4-handover` 标签。
运行目录保存 `map-config.json` 及原子更新的最新 `map-state.json`；
观察进程的错误显示在地图及 `docker logs MAP_CONTAINER` 中。
S4 的坐标摘录约 4 MB；提取时读到离线观察文件的 positions 字段末尾即停止。
