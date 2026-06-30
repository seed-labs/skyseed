# export_satellite_links.py

生成“卫星 - 卫星”连接关系，并输出一个 JSON payload。

这是可选附带工具。如果对方只需要卫星/GW 连接，使用
`export_ground_links.py` 即可，可以忽略本文件。

本工具需要和 `export_ground_links.py` 放在同一目录，因为它复用了同目录里的
standalone planned-shell/TLE 轨道采样辅助逻辑。

输出格式固定为：

```json
{
  "timestamp": "1782439276134",
  "interval": "1s",
  "satelliteLinks": [
    [
      {
        "satelliteAId": "940001",
        "satelliteBId": "940002"
      }
    ]
  ]
}
```

含义：

- `timestamp`：起始时间戳，毫秒字符串。
- `interval`：采样间隔，例如 `1s`、`30s`。
- `satelliteLinks[i]`：第 `i` 个采样时刻的所有卫星/卫星连接。
- 每条连接只包含 `satelliteAId` 和 `satelliteBId`。

默认只打印或保存 JSON；只有显式传入 `--post-url` 时才会 POST。

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

- `--planned-shell S1|S2|S3|S4|S5`：使用内置的确定性 shell。默认：`S5`。
- `--tle PATH`：使用三行格式 TLE 文件；传它时不再使用 `--planned-shell`。
- `--start-timestamp VALUE`：起始时间，支持毫秒时间戳或 ISO-8601。默认：`1782439276134`。
- `--interval VALUE`：采样间隔，例如 `1s`、`30s`。默认：`1s`。
- `--steps N`：采样点数量。默认：`3`，除非使用 `--duration`。
- `--duration VALUE`：按总时长和 interval 自动推导 steps。
- `--max-satellites N`：本工具最多使用多少颗卫星。默认：`24`。
- `--satellite-id-format norad|node-id`：payload 里的卫星 id 格式。默认：`norad`。
- `--dry-run-json [PATH]`：不传路径则打印到 stdout，传路径则保存到文件。默认：stdout。
- `--post-url URL`：POST 同一份 JSON payload。默认不 POST。

常用星间链路控制参数：

- `--no-same-plane`：不输出同轨道面 ring 链路。
- `--no-cross-plane`：不输出跨轨道面动态链路。
- `--cross-plane-strategy nearest-adjacent-plane|same-index`：跨轨选择策略。默认：`nearest-adjacent-plane`。
- `--cross-plane-candidate-window N`：相邻轨道面搜索窗口。默认：`5`。
- `--max-cross-plane-distance-km N`：跨轨链路最大 3D 距离。默认：`3300`。
- `--cross-plane-hysteresis-km N`：保留上一时刻跨轨连接的距离余量。默认：`150`。

## 连接选择逻辑

同轨道面链路：同一个 orbital plane 内，卫星按面内位置排序，每颗卫星连接下一颗，
形成 ring。

跨轨道面链路：默认只在相邻 orbital planes 之间选择。每颗卫星在相邻面的小窗口内
寻找候选，按 3D 距离做 deterministic 的一对一最近匹配，并用最大距离阈值和
hysteresis 减少连接震荡。

## 文件夹内容

外部原型包放在 `visualization/`，包含四个文件：

- `export_ground_links.py`
- `export_ground_links_USAGE.md`
- `export_satellite_links.py`
- `export_satellite_links_USAGE.md`
