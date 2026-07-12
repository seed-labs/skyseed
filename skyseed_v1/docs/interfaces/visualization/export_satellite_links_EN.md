# Satellite-link visualization interface

This exporter produces satellite-to-satellite links as JSON. Prefer unified
`export_links.py` for normal integration; this focused tool remains for ISL
debugging and always emits an empty `groundLinks` array.

```json
{
  "timestamp": "1782439276134",
  "interval": "1s",
  "links": [{
    "groundLinks": [],
    "satelliteLinks": [{"satelliteAId": "940001", "satelliteBId": "940002"}]
  }]
}
```

The default action prints or saves strict JSON. POST is opt-in through
`--post-url`, uses body `{}`, and leaves data in the generated file.

## Examples

```bash
python -m skyseed_v1.visualization.export_satellite_links \
  --planned-shell S4 --start-timestamp 1782439276134 --interval 1s --steps 3

python -m skyseed_v1.visualization.export_satellite_links \
  --planned-shell S4 --steps 3 --dry-run-json satellite_links.json

python -m skyseed_v1.visualization.export_satellite_links \
  --planned-shell S4 --steps 3 \
  --post-url http://example.test/api/v1/satellite/links
```

## Main arguments

- `--planned-shell S1|S2|S3|S4|S5|ALL|S1-S5`: deterministic shell, default S5.
- `--tle PATH`: three-line TLE input instead of a planned shell.
- `--planned-orbit-data PATH`: fixed orbit JSON, defaulting to the canonical S1–S5 file.
- `--start-timestamp`, `--interval`, `--steps`, `--duration`: time range.
- `--max-satellites N`: zero means unlimited.
- `--satellite-id-format norad|node-id`: default `norad`.
- `--dry-run-json [PATH]`: stdout or file output.
- `--post-url URL`: trigger POST with `{}`.

ISL controls include `--no-same-plane`, `--no-cross-plane`,
`--cross-plane-strategy nearest-adjacent-plane|same-index`,
`--cross-plane-candidate-window` (default 5),
`--max-cross-plane-distance-km` (default 3300), and
`--cross-plane-hysteresis-km` (default 150).

## Fixed orbit data

Share `planned_shell_orbit_data_s1_s5.json` with the visualizer so positions
match exactly. Regenerate it only for a deliberate contract update:

```bash
python export_planned_shell_orbit_data.py \
  --shell-id all \
  --start-time 2026-01-01T00:00:00Z \
  --records-only \
  --output planned_shell_orbit_data_s1_s5.json
```

## Link selection

This is a deterministic visualization-integration ISL prototype, not a full
laser-link physics model. Same-plane satellites are ordered within each plane
and connected as a ring unless `--no-same-plane` is set.

Cross-plane links normally connect adjacent planes within the same shell. The
default `nearest-adjacent-plane` strategy searches a bounded index window,
sorts candidates by 3D distance, and performs deterministic one-to-one
matching. Links must satisfy the maximum distance. A previous link may remain
within the hysteresis margin to reduce oscillation. `ALL` never joins different
shells. `same-index` instead proposes equal-index neighbors before applying the
same distance filter. Output order is deterministic.

Generated debug files such as `ground-links.json`, `satellite-links.json`, and
`globe.html` are not required fixed assets.
