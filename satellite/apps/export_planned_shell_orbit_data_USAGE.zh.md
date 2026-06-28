# Planned Shell 轨道数据导出脚本使用说明

这份说明配套 `export_planned_shell_orbit_data.py` 使用。脚本是自包含的，只依赖 Python 标准库；把这个 `.py` 文件单独发给别人，对方也可以直接运行。

## 作用

脚本用于生成 `S1`、`S2`、`S3`、`S4`、`S5` planned-shell 的确定性轨道数据。

它输出两类核心数据：

- `selected_records`：每颗卫星的轨道参数，例如倾角、RAAN、mean anomaly、mean motion。
- `propagated_positions.slices`：按时间片传播后的卫星位置和速度，包括 `x/y/z` 和 `vx/vy/vz`。

注意：这个脚本生成的是 planned-shell 圆轨道模型数据，不是实时 TLE / CelesTrak / SGP4 数据。

## 基本命令

```bash
python3 export_planned_shell_orbit_data.py \
  --shell-id S4 \
  --output s4_orbit_data.json
```

如果脚本还在项目目录里，可以这样运行：

```bash
python3 satellite/apps/export_planned_shell_orbit_data.py \
  --shell-id S4 \
  --output /tmp/s4_orbit_data.json
```

## 推荐示例

生成 full S4，348 颗卫星，从固定时间开始，只输出 1 个时间片：

```bash
python3 export_planned_shell_orbit_data.py \
  --shell-id S4 \
  --max-satellites 348 \
  --start-time 2026-01-01T00:00:00Z \
  --step-seconds 60 \
  --slice-count 1 \
  --output s4_full_orbit_data.json
```

生成 full S4，348 颗卫星，每 60 秒一个时间片，共 10 分钟：

```bash
python3 export_planned_shell_orbit_data.py \
  --shell-id S4 \
  --max-satellites 348 \
  --start-time 2026-01-01T00:00:00Z \
  --step-seconds 60 \
  --horizon-seconds 600 \
  --output s4_full_10min_orbit_data.json
```

生成抽样版 S4，只导出 24 颗卫星：

```bash
python3 export_planned_shell_orbit_data.py \
  --shell-id S4 \
  --max-satellites 24 \
  --start-time 2026-01-01T00:00:00Z \
  --step-seconds 120 \
  --slice-count 3 \
  --output s4_sample_24_orbit_data.json
```

只输出轨道参数，不输出传播后的时间片位置：

```bash
python3 export_planned_shell_orbit_data.py \
  --shell-id S4 \
  --max-satellites 348 \
  --records-only \
  --output s4_orbit_records_only.json
```

## 参数说明

### `--shell-id`

必填。选择 planned shell。

可选值：

```text
S1, S2, S3, S4, S5
```

示例：

```bash
--shell-id S4
```

### `--max-satellites`

可选。指定导出多少颗卫星。

如果不传，默认导出该 shell 的全部卫星：

```text
S1 = 1584
S2 = 1584
S3 = 720
S4 = 348
S5 = 172
```

示例：

```bash
--max-satellites 348
```

如果传入的数量小于完整 shell 容量，脚本会按轨道面均衡抽样。例如：

```bash
--max-satellites 24
```

### `--start-time`

可选。轨道传播开始时间，使用 ISO-8601 UTC 格式。

示例：

```bash
--start-time 2026-01-01T00:00:00Z
```

如果不传，默认使用当前 UTC 时间。

### `--step-seconds`

可选。两个轨道时间片之间的间隔，单位是秒。

默认值：

```text
120.0
```

示例：

```bash
--step-seconds 60
```

### `--slice-count`

可选。生成多少个时间片。

默认值：

```text
1
```

示例：

```bash
--slice-count 5
```

如果同时传了 `--horizon-seconds`，脚本会优先使用 `--horizon-seconds` 计算时间片数量。

### `--horizon-seconds`

可选。指定总时间范围，单位是秒。脚本会用下面的公式计算时间片数量：

```text
floor(horizon_seconds / step_seconds) + 1
```

示例：

```bash
--step-seconds 60 --horizon-seconds 600
```

这会生成 11 个时间片：

```text
0s, 60s, 120s, ..., 600s
```

### `--records-only`

可选。只输出轨道参数，不输出传播后的时间片位置。

示例：

```bash
--records-only
```

使用这个参数时，输出 JSON 仍然包含：

```text
shell_preset
requested
shell_selection
selected_records
```

但不会包含：

```text
propagated_positions
```

适合只需要轨道定义、RAAN、mean anomaly、mean motion 等参数，而不需要 `x/y/z` 采样点的情况。

### `--output`

可选。输出 JSON 文件路径。

示例：

```bash
--output s4_orbit_data.json
```

如果不传，脚本会把 JSON 直接打印到终端。

## 输出 JSON 说明

输出文件是一个 JSON 对象，主要字段如下：

```text
schema_version
orbit_input_mode
orbit_input_kind
orbit_source
shell_preset
requested
full_record_count
selected_record_count
shell_selection
selected_records
propagated_positions
```

如果使用了 `--records-only`，顶层不会出现 `propagated_positions`。

### 顶层字段

`schema_version`

输出格式版本。

`orbit_input_mode`

固定为：

```text
planned-shell
```

`orbit_input_kind`

固定为：

```text
planned_shell_circular_orbit
```

表示这是 planned-shell 圆轨道传播模型。

`orbit_source`

轨道来源标识，例如：

```text
starlink_phase1_S4
```

`shell_preset`

所选 shell 的基础参数。例如 S4：

```json
{
  "altitude_km": 560.0,
  "inclination_deg": 97.6,
  "plane_count": 6,
  "satellite_count": 348,
  "satellites_per_plane": 58,
  "shell_id": "S4"
}
```

`requested`

本次命令的实际请求参数，包括：

```text
shell_id
max_satellites
start_time_utc
step_seconds
slice_count
horizon_seconds
```

`full_record_count`

该 shell 完整容量。例如 full S4 是 `348`。

`selected_record_count`

本次实际导出的卫星数量。如果 `--max-satellites 24`，这里就是 `24`。

### `shell_selection`

说明卫星选择结果，包含：

```text
selection_method
cluster_inclination_bucket_deg
cluster_altitude_bucket_km
cluster_record_count
plane_count_in_cluster
selected_plane_count
selected_satellite_count
included_satellite_ids
parameter_bands
plane_manifest
```

常用字段：

- `selected_satellite_count`：实际选出的卫星数量。
- `included_satellite_ids`：选中的合成 NORAD ID 列表。
- `plane_manifest`：每个轨道面选中了哪些卫星。

### `selected_records`

这是轨道参数列表，每一项代表一颗卫星。

单颗卫星示例：

```json
{
  "argument_of_perigee_deg": 0.0,
  "eccentricity": 0.0,
  "epoch_utc": "2026-01-01T00:00:00Z",
  "inclination_deg": 97.6,
  "line1": "PLANNED S4 940001",
  "line2": "97.6000 0.0000 0.0000 14.860957642",
  "mean_anomaly_deg": 0.0,
  "mean_motion_rev_per_day": 14.860957642,
  "norad_id": 940001,
  "raan_deg": 0.0,
  "satellite_name": "STARLINK-S4-P01-S01"
}
```

字段说明：

- `satellite_name`：卫星名称。
- `norad_id`：脚本生成的合成卫星 ID。
- `epoch_utc`：轨道参数 epoch，默认是 `2026-01-01T00:00:00Z`。
- `inclination_deg`：轨道倾角。
- `raan_deg`：升交点赤经。
- `mean_anomaly_deg`：平近点角。
- `mean_motion_rev_per_day`：每天绕行圈数。
- `eccentricity`：离心率。planned-shell 模型中固定为 `0.0`。
- `argument_of_perigee_deg`：近地点幅角。planned-shell 模型中固定为 `0.0`。

### `propagated_positions`

这是传播后的时间片数据。

结构：

```text
propagated_positions.start_time_utc
propagated_positions.step_seconds
propagated_positions.slice_count
propagated_positions.slices
```

`slices` 中每一项是一个时间片。

时间片示例：

```json
{
  "relative_time_seconds": 0.0,
  "slice_id": "starlink-shell-slice-0001",
  "timestamp_utc": "2026-01-01T00:00:00Z",
  "satellite_states": {
    "stl-940001": {
      "norad_id": 940001,
      "satellite_node_id": "stl-940001",
      "timestamp_utc": "2026-01-01T00:00:00Z",
      "vx_km_s": 0.0,
      "vy_km_s": -0.986885471,
      "vz_km_s": 7.499895747,
      "x_km": 6931.0,
      "y_km": 0.0,
      "z_km": 0.0
    }
  }
}
```

字段说明：

- `slice_id`：时间片 ID。
- `relative_time_seconds`：相对开始时间的秒数。
- `timestamp_utc`：该时间片的 UTC 时间。
- `satellite_states`：这个时间片内所有卫星的位置和速度。

每颗卫星状态字段：

- `satellite_node_id`：节点 ID，格式是 `stl-<norad_id>`。
- `norad_id`：合成卫星 ID。
- `x_km`, `y_km`, `z_km`：ECI-like 坐标位置，单位 km。
- `vx_km_s`, `vy_km_s`, `vz_km_s`：速度向量，单位 km/s。

## 快速检查输出

可以用下面命令检查 JSON 是否有效：

```bash
python3 -m json.tool s4_orbit_data.json >/dev/null
```

查看导出的卫星数量：

```bash
python3 - <<'PY'
import json
with open("s4_orbit_data.json", "r", encoding="utf-8") as f:
    data = json.load(f)
print(data["selected_record_count"])
PY
```

查看第一个时间片里某颗卫星的位置：

```bash
python3 - <<'PY'
import json
with open("s4_orbit_data.json", "r", encoding="utf-8") as f:
    data = json.load(f)
state = data["propagated_positions"]["slices"][0]["satellite_states"]["stl-940001"]
print(state)
PY
```

## S4 full shell 的确认值

使用下面命令：

```bash
python3 export_planned_shell_orbit_data.py \
  --shell-id S4 \
  --max-satellites 348 \
  --start-time 2026-01-01T00:00:00Z \
  --slice-count 1 \
  --output s4_full_orbit_data.json
```

应得到：

```text
selected_record_count = 348
first satellite = STARLINK-S4-P01-S01 / 940001 / stl-940001
last satellite = STARLINK-S4-P06-S58 / 940348 / stl-940348
```
