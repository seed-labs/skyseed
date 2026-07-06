# export_satellite_links.py

生成“卫星 - 卫星”连接关系，并输出一个 JSON payload。

新版统一连接文件建议直接使用 `export_links.py`。本工具保留给只调试
卫星/卫星连接时使用；输出里 `groundLinks` 会是空数组。

本工具需要和 `export_ground_links.py` 放在同一目录，因为它复用了同目录里的
standalone planned-shell/TLE 轨道采样辅助逻辑。

输出格式固定为：

```json
{
  "timestamp": "1782439276134",
  "interval": "1s",
  "links": [
    {
      "groundLinks": [],
      "satelliteLinks": [
        {
          "satelliteAId": "940001",
          "satelliteBId": "940002"
        }
      ]
    }
  ]
}
```

含义：

- `timestamp`：起始时间戳，毫秒字符串。
- `interval`：采样间隔，例如 `1s`、`30s`。
- `links[i].groundLinks`：本工具中固定为空数组。
- `links[i].satelliteLinks`：第 `i` 个采样时刻的所有卫星/卫星连接。
- 每条连接只包含 `satelliteAId` 和 `satelliteBId`。

默认只打印或保存 JSON；只有显式传入 `--post-url` 时才会 POST。POST body 为 `{}`，
连接数据不再作为请求 body 发送。
`satellite-links.json` 这类结果文件不是固定输入文件，需要时用 `--dry-run-json` 重新生成。

## 使用示例

建议在 `visualization/` 目录下运行，也可以传完整脚本路径。

dry-run 输出到 stdout：

```bash
python export_satellite_links.py \
  --planned-shell S4 \
  --start-timestamp 1782439276134 \
  --interval 1s \
  --steps 3
```

dry-run 保存到文件：

```bash
python export_satellite_links.py \
  --planned-shell S4 \
  --steps 3 \
  --dry-run-json satellite_links.json
```

POST：

```bash
python export_satellite_links.py \
  --planned-shell S4 \
  --start-timestamp 1782439276134 \
  --interval 1s \
  --steps 3 \
  --post-url http://example.test/api/v1/satellite/links
```

HTTP 失败时会打印 status code 和 response body。

## 主要参数

- `--planned-shell S1|S2|S3|S4|S5|ALL|S1-S5`：使用内置的确定性 shell。默认：`S5`；`ALL`/`S1-S5` 表示同时使用 S1-S5。
- `--tle PATH`：使用三行格式 TLE 文件；传它时不再使用 `--planned-shell`。
- `--planned-orbit-data PATH`：固定 planned shell 轨道数据 JSON。默认会自动使用同目录的 `planned_shell_orbit_data_s1_s5.json`；如果该文件不存在，才回退到脚本内置的 planned shell 生成逻辑。
- `--start-timestamp VALUE`：起始时间，支持毫秒时间戳或 ISO-8601。默认使用当前 UTC 时间；需要复现实验时再显式传固定值。
- `--interval VALUE`：采样间隔，例如 `1s`、`30s`。默认：`1s`。
- `--steps N`：采样点数量。默认：`3`，除非使用 `--duration`。
- `--duration VALUE`：按总时长和 interval 自动推导 steps。
- `--max-satellites N`：本工具最多使用多少颗卫星。默认：`0`，表示不限制。
- `--satellite-id-format norad|node-id`：payload 里的卫星 id 格式。默认：`norad`。
- `--dry-run-json [PATH]`：不传路径则打印到 stdout，传路径则保存到文件。默认：stdout。
- `--post-url URL`：发送 POST 触发请求，body 为 `{}`。默认不 POST。

常用星间链路控制参数：

- `--no-same-plane`：不输出同轨道面 ring 链路。
- `--no-cross-plane`：不输出跨轨道面动态链路。
- `--cross-plane-strategy nearest-adjacent-plane|same-index`：跨轨选择策略。默认：`nearest-adjacent-plane`。
- `--cross-plane-candidate-window N`：相邻轨道面搜索窗口。默认：`5`。
- `--max-cross-plane-distance-km N`：跨轨链路最大 3D 距离。默认：`3300`。
- `--cross-plane-hysteresis-km N`：保留上一时刻跨轨连接的距离余量。默认：`150`。

## 固定轨道数据

为了让星间连接和外部可视化里的卫星位置完全一致，建议把同目录的
`planned_shell_orbit_data_s1_s5.json` 和本工具一起给对方使用。该文件固定了
S1-S5 全部 planned 卫星的轨道记录；本工具在 planned-shell 模式下会默认优先读取它。

重新生成这份固定数据：

```bash
python export_planned_shell_orbit_data.py \
  --shell-id all \
  --start-time 2026-01-01T00:00:00Z \
  --records-only \
  --output planned_shell_orbit_data_s1_s5.json
```

## 连接选择逻辑

这是一个用于可视化联调的 deterministic ISL 原型模型，不是完整激光链路物理模型。
默认同时输出同轨道面链路和跨轨道面链路。

同轨道面链路：

- 同一个 orbital plane 内，卫星按面内位置排序；
- 每颗卫星连接下一颗卫星，最后一颗再连回第一颗，形成 ring；
- 这类链路默认每个采样时刻都会输出；
- 可用 `--no-same-plane` 关闭。

跨轨道面链路：

- 默认只在相邻 orbital planes 之间选择；
- `ALL`/`S1-S5` 模式下，不会把不同 shell 边界误连成跨轨链路；
- 默认策略是 `nearest-adjacent-plane`：每颗卫星只在相邻面附近的小窗口内找候选；
- 搜索窗口由 `--cross-plane-candidate-window` 控制，默认 `5`，表示面内编号附近正负 5 个候选；
- 候选按 3D 距离排序，做 deterministic 的一对一最近匹配：一颗卫星在同一对相邻面中最多参与一个跨轨匹配；
- 跨轨距离必须 `<= --max-cross-plane-distance-km`，默认 `3300 km`；
- 如果上一时刻已有跨轨连接，且距离没有超过阈值加 `--cross-plane-hysteresis-km`，默认额外保留 `150 km` 的 hysteresis margin，用来减少连接震荡；
- 可用 `--no-cross-plane` 关闭。

如果使用 `--cross-plane-strategy same-index`，跨轨候选会退回到“相邻面同编号卫星”模式，
然后仍然按最大距离阈值过滤。

输出顺序是 deterministic，同样输入会得到同样 JSON。

## 文件夹内容

外部原型包放在 `visualization/`，核心文件包括：

- `export_ground_links.py`
- `export_ground_links_USAGE.md`
- `export_links.py`
- `export_links_USAGE.md`
- `export_satellite_links.py`
- `export_satellite_links_USAGE.md`
- `export_planned_shell_orbit_data.py`
- `planned_shell_orbit_data_s1_s5.json`
- `starlink_gateways.json`

`ground-links.json`、`satellite-links.json`、`globe.html` 等是调试/运行产物，不属于必须随包保存的固定文件。
