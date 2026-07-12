# Unified visualization links interface

生成对方新版可视化接口使用的统一连接文件。

输出格式：

```json
{
  "timestamp": "1782950400000",
  "interval": "1s",
  "links": [
    {
      "groundLinks": [
        {
          "satelliteId": "940158",
          "groundStationId": "starlink-gs-0002-akita-japan"
        }
      ],
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
- `interval`：采样间隔，例如 `1s`。
- `links[i]`：第 `i` 个采样时刻的连接集合。
- `links[i].groundLinks`：该时间片的卫星/GW 连接；没有则为空数组。
- `links[i].satelliteLinks`：该时间片的卫星/卫星连接；没有则为空数组。

## 使用示例

建议在 `visualization/` 目录下运行。

生成统一连接文件：

```bash
python -m skyseed_v1.visualization.export_links \
  --planned-shell S4 \
  --start-timestamp 1782950400000 \
  --interval 1s \
  --steps 300 \
  --dry-run-json /path/to/fixed/links.json
```

生成 S1-S5 全部：

```bash
python -m skyseed_v1.visualization.export_links \
  --planned-shell ALL \
  --start-timestamp 1782950400000 \
  --interval 1s \
  --steps 300 \
  --dry-run-json /path/to/fixed/links.json
```

写入固定路径后触发后端读取：

```bash
python -m skyseed_v1.visualization.export_links \
  --planned-shell S4 \
  --start-timestamp 1782950400000 \
  --interval 1s \
  --steps 300 \
  --dry-run-json /path/to/fixed/links.json \
  --post-url http://localhost:9091/api/v1/satellite/ground-links
```

使用 `--post-url` 时，请求 body 为 `{}`；连接数据不再作为 POST body 发送。
后端应从约定好的固定文件路径读取 `--dry-run-json` 生成的 JSON。

## 固定输入文件

- `planned_shell_orbit_data_s1_s5.json`：S1-S5 的固定 planned 轨道记录，用于保证双方卫星位置一致。
- `starlink_gateways.json`：默认 GW catalog。

默认会自动读取同目录的这两个文件。
