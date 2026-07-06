# export_ground_links.py

生成“卫星 - 地面站”连接关系，并输出一个 JSON payload。

新版统一连接文件建议直接使用 `export_links.py`。本工具保留给只调试
卫星/GW 连接时使用；输出里 `satelliteLinks` 会是空数组。

输出格式固定为：

```json
{
  "timestamp": "1782439276134",
  "interval": "1s",
  "links": [
    {
      "groundLinks": [
        {
          "satelliteId": "940014",
          "groundStationId": "gw1"
        }
      ],
      "satelliteLinks": []
    }
  ]
}
```

实际 POST 的内容是严格 JSON，不包含 `#` 注释。

含义：

- `timestamp`：起始时间戳，毫秒字符串。
- `interval`：采样间隔，例如 `1s`、`30s`。
- `links[i].groundLinks`：第 `i` 个采样时刻的所有卫星/GW 连接。
- `links[i].satelliteLinks`：本工具中固定为空数组。
- 每条连接只包含 `satelliteId` 和 `groundStationId`。

默认只打印或保存 JSON；只有显式传入 `--post-url` 时才会 POST。POST body 为 `{}`，
连接数据不再作为请求 body 发送。
`ground-links.json` 这类结果文件不是固定输入文件，需要时用 `--dry-run-json` 重新生成。

## 使用示例

建议在 `visualization/` 目录下运行，也可以传完整脚本路径。

dry-run 输出到 stdout：

```bash
python export_ground_links.py \
  --planned-shell S4 \
  --start-timestamp 1782439276134 \
  --interval 1s \
  --steps 3
```

dry-run 保存到文件：

```bash
python export_ground_links.py \
  --planned-shell S4 \
  --steps 3 \
  --dry-run-json ground_links.json
```

POST 到接口：

```bash
python export_ground_links.py \
  --planned-shell S4 \
  --start-timestamp 1782439276134 \
  --interval 1s \
  --steps 3 \
  --post-url http://192.168.23.128:9091/api/v1/satellite/ground-links
```

HTTP 失败时会打印 status code 和 response body。

## 主要参数

- `--planned-shell S1|S2|S3|S4|S5|ALL|S1-S5`：使用内置的确定性 shell。默认：`S5`；`ALL`/`S1-S5` 表示同时使用 S1-S5。
- `--tle PATH`：使用三行格式 TLE 文件；传它时不再使用 `--planned-shell`。
- `--planned-orbit-data PATH`：固定 planned shell 轨道数据 JSON。默认会自动使用同目录的 `planned_shell_orbit_data_s1_s5.json`；如果该文件不存在，才回退到脚本内置的 planned shell 生成逻辑。
- `--ground-station-catalog PATH`：地面站 JSON catalog。默认使用同目录的 `starlink_gateways.json`；如果该文件不存在，才回退到内置 demo GW。
- `--ground-station-ids a,b,c`：只使用指定 GW id。
- `--max-ground-stations N`：未指定 ids 时最多使用多少个 GW。默认不限制。
- `--start-timestamp VALUE`：起始时间，支持毫秒时间戳或 ISO-8601。默认使用当前 UTC 时间；需要复现实验时再显式传固定值。
- `--interval VALUE`：采样间隔，例如 `1s`、`30s`。默认：`1s`。
- `--steps N`：采样点数量。默认：`3`，除非使用 `--duration`。
- `--duration VALUE`：按总时长和 interval 自动推导 steps。
- `--max-satellites N`：本工具最多使用多少颗卫星。默认：`0`，表示不限制。
- `--dry-run-json [PATH]`：不传路径则打印到 stdout，传路径则保存到文件。默认：stdout。
- `--post-url URL`：发送 POST 触发请求，body 为 `{}`。默认不 POST。

常用链路过滤参数：

- `--min-elevation-deg`：最低仰角。默认：`5`。
- `--max-distance-km`：卫星/GW 最大距离。默认：`2500` km；传 `0` 或负数表示关闭距离上限。
- `--max-satellites-per-ground-station`：可选，单个 GW 的卫星连接容量；`0` 表示不限。
- `--satellite-id-format norad|node-id`：payload 里的卫星 id 格式。默认：`norad`。

## 自定义 GW Catalog

GW catalog 是 JSON 数组：

```json
[
  {
    "groundStationId": "starlink-gs-0178-puerto-montt-chile",
    "latitude_deg": -41.47,
    "longitude_deg": -72.94,
    "altitude_km": 0.0,
    "max_satellite_links": 8
  }
]
```

可识别的 id 字段：

```text
groundStationId, ground_station_id, gateway_id, station_id, id
```

可识别的坐标字段：

```text
latitude_deg/longitude_deg, latitude/longitude, lat/lon
```

可识别的高度字段：

```text
altitude_km, altitude, altitudeMeters, altitude_m
```

同目录里的默认 `starlink_gateways.json` 使用 `id`、`latitude`、`longitude`、
`altitudeMeters` 字段，工具会直接识别。

## 固定轨道数据

为了让连接关系和外部可视化里的卫星位置完全一致，建议把同目录的
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

默认策略是“每颗卫星选择一个最佳可见 GW”。注意这里的“最佳”不是单纯最近，
所以某些卫星没有选择距离最近的基站是正常现象。

每个采样时刻大致按下面步骤处理：

1. 先计算卫星位置和 GW 位置。
2. 生成候选连接，只保留满足条件的卫星/GW：
   - 卫星相对 GW 的仰角 `>= --min-elevation-deg`，默认 `5` 度；
   - 距离必须 `<= --max-distance-km`，默认 `2500 km`；传 `0` 或负数可关闭该硬阈值；
   - 如果设置了正数 `--earth-clearance-km`，链路线段需要满足额外地球净空；
   - 如果 GW catalog 或 `--max-satellites-per-ground-station` 设置了容量，还会受容量限制。
3. 对每颗卫星的所有候选 GW 打分并选择一个。

默认打分公式可以理解为：

```text
score = elevation_deg * 1.0
        - distance_km * 0.002
        - handover_penalty
        - load_penalty
```

其中：

- `elevation_deg` 越高越好；
- `distance_km` 越近越好，但它只是分数的一部分，不是唯一标准；
- 如果上一时刻已经连着某个 GW，切到别的 GW 会有默认 `3.0` 的 handover penalty；
- 已经被分配较多卫星的 GW 会有默认 `0.5` 的 load penalty，用来避免所有卫星过度集中到同一个 GW；
- 默认还有 `min_hold_steps=2` 和 `hysteresis_elevation_deg=2`，用于减少相邻时间片之间来回震荡。

因此，A 基站即使距离更近，也可能因为仰角更低、当前负载更高，或者切换代价不划算，
而输给另一个稍远但更稳定/仰角更好的基站。

默认情况下也会出现“某颗卫星没有 GW 连接”的情况：如果它在该时间片没有任何 GW
同时满足最低仰角和最大距离阈值，就不会输出连接。

性能优化里的空间网格只用于少算明显不可能可见的候选；最终是否输出连接仍由上面的
精确仰角、距离、容量和打分逻辑决定。

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
