# export_ground_links.py

生成“卫星 - 地面站”连接关系，并输出一个 JSON payload。

这是主要交付工具。通常对方只需要先接这个 `groundLinks` payload；同目录里的
`export_satellite_links.py` 是可选附带工具，只有对方也需要星间链路时再使用。

输出格式固定为：

```json
{
  "timestamp": "1782439276134",
  "interval": "1s",
  "groundLinks": [
    [
      {
        "satelliteId": "940014",
        "groundStationId": "gw1"
      }
    ]
  ]
}
```

实际 POST 的内容是严格 JSON，不包含 `#` 注释。

含义：

- `timestamp`：起始时间戳，毫秒字符串。
- `interval`：采样间隔，例如 `1s`、`30s`。
- `groundLinks[i]`：第 `i` 个采样时刻的所有卫星/GW 连接。
- 每条连接只包含 `satelliteId` 和 `groundStationId`。

默认只打印或保存 JSON；只有显式传入 `--post-url` 时才会 POST。

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

- `--planned-shell S1|S2|S3|S4|S5`：使用内置的确定性 shell。默认：`S5`。
- `--tle PATH`：使用三行格式 TLE 文件；传它时不再使用 `--planned-shell`。
- `--ground-station-catalog PATH`：地面站 JSON catalog。默认使用内置 demo GW。
- `--ground-station-ids a,b,c`：只使用指定 GW id。
- `--max-ground-stations N`：未指定 ids 时最多使用多少个 GW。默认：`3`。
- `--start-timestamp VALUE`：起始时间，支持毫秒时间戳或 ISO-8601。默认：`1782439276134`。
- `--interval VALUE`：采样间隔，例如 `1s`、`30s`。默认：`1s`。
- `--steps N`：采样点数量。默认：`3`，除非使用 `--duration`。
- `--duration VALUE`：按总时长和 interval 自动推导 steps。
- `--max-satellites N`：本工具最多使用多少颗卫星。默认：`24`。
- `--dry-run-json [PATH]`：不传路径则打印到 stdout，传路径则保存到文件。默认：stdout。
- `--post-url URL`：POST 同一份 JSON payload。默认不 POST。

常用链路过滤参数：

- `--min-elevation-deg`：最低仰角。默认：`5`。
- `--max-distance-km`：可选，卫星/GW 最大距离。
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

## 连接选择逻辑

每个采样时刻先计算可见的卫星/GW 候选连接，再按仰角、地球视线遮挡、
可选距离上限和可选 GW 容量过滤。默认策略是“每颗卫星选择一个最佳可见
GW”，评分主要考虑仰角和距离，并带有 hysteresis/min-hold 逻辑来减少频繁切换。

输出顺序是 deterministic，同样输入会得到同样 JSON。

## 文件夹内容

外部原型包放在 `visualization/`，包含四个文件：

- `export_ground_links.py`
- `export_ground_links_USAGE.md`
- `export_satellite_links.py`
- `export_satellite_links_USAGE.md`
