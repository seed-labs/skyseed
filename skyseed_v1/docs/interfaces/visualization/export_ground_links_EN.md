# Ground-link visualization interface

This exporter produces satellite-to-ground-station links as JSON. Prefer the
unified `export_links.py` interface for normal integration; this focused tool
remains useful for debugging satellite/GW links and always emits an empty
`satelliteLinks` array.

```json
{
  "timestamp": "1782439276134",
  "interval": "1s",
  "links": [{
    "groundLinks": [{"satelliteId": "940014", "groundStationId": "gw1"}],
    "satelliteLinks": []
  }]
}
```

The default action prints or saves strict JSON. A POST occurs only when
`--post-url` is supplied, and its body is `{}`; the generated file remains the
data handoff. Files such as `ground-links.json` are generated outputs, not
fixed inputs.

## Examples

```bash
python -m skyseed_v1.visualization.export_ground_links \
  --planned-shell S4 \
  --start-timestamp 1782439276134 \
  --interval 1s \
  --steps 3

python -m skyseed_v1.visualization.export_ground_links \
  --planned-shell S4 --steps 3 --dry-run-json ground_links.json

python -m skyseed_v1.visualization.export_ground_links \
  --planned-shell S4 --steps 3 \
  --post-url http://192.168.23.128:9091/api/v1/satellite/ground-links
```

HTTP failures print the status code and response body.

## Main arguments

- `--planned-shell S1|S2|S3|S4|S5|ALL|S1-S5`: deterministic built-in shell; default S5. `ALL` and `S1-S5` include every shell.
- `--tle PATH`: use a three-line TLE file instead of a planned shell.
- `--planned-orbit-data PATH`: fixed planned-orbit JSON; defaults to the canonical S1–S5 file and only falls back to built-in generation when absent.
- `--ground-station-catalog PATH`: gateway JSON; defaults to `starlink_gateways.json`, then to built-in demo gateways only if absent.
- `--ground-station-ids a,b,c` and `--max-ground-stations N`: select gateways.
- `--start-timestamp VALUE`: milliseconds or ISO-8601; current UTC by default, so supply it for reproducibility.
- `--interval`, `--steps`, `--duration`: sample cadence and extent.
- `--max-satellites N`: zero means unlimited.
- `--dry-run-json [PATH]`: stdout without a path, otherwise write the file.
- `--post-url URL`: trigger POST with `{}`.

Common link filters are `--min-elevation-deg` (default 5),
`--max-distance-km` (default 2500; non-positive disables it),
`--max-satellites-per-ground-station` (zero means unlimited), and
`--satellite-id-format norad|node-id` (default `norad`).

## Gateway catalog

The catalog is a JSON array. A typical item is:

```json
{
  "groundStationId": "starlink-gs-0178-puerto-montt-chile",
  "latitude_deg": -41.47,
  "longitude_deg": -72.94,
  "altitude_km": 0.0,
  "max_satellite_links": 8
}
```

Recognized IDs are `groundStationId`, `ground_station_id`, `gateway_id`,
`station_id`, or `id`. Coordinates may be `latitude_deg`/`longitude_deg`,
`latitude`/`longitude`, or `lat`/`lon`. Altitude may use `altitude_km`,
`altitude`, `altitudeMeters`, or `altitude_m`.

## Fixed orbit data

Use the canonical `planned_shell_orbit_data_s1_s5.json` with the external
visualizer so positions match exactly. Regenerate it only when deliberately
updating that contract:

```bash
python export_planned_shell_orbit_data.py \
  --shell-id all \
  --start-time 2026-01-01T00:00:00Z \
  --records-only \
  --output planned_shell_orbit_data_s1_s5.json
```

## Link selection

The default policy chooses one best visible GW for each satellite. “Best” is
not simply nearest. Each sample computes positions, filters candidates by
elevation, distance, optional Earth clearance, and capacity, then scores them:

```text
score = elevation_deg * 1.0
        - distance_km * 0.002
        - handover_penalty
        - load_penalty
```

The default handover penalty is 3.0 and load penalty is 0.5. A default
`min_hold_steps=2` and `hysteresis_elevation_deg=2` reduce oscillation. A
slightly farther gateway can therefore win because it has higher elevation,
lower load, or avoids a handover. A satellite emits no ground link when no
candidate satisfies the thresholds. Spatial-grid optimization only skips
obviously impossible candidates; exact filtering and scoring remain
authoritative. Output order is deterministic.

Generated debug files such as `ground-links.json`, `satellite-links.json`, and
`globe.html` are not required fixed assets.
