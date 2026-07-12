# Unified visualization links interface

This exporter produces the unified link file consumed by the current visualization interface.

```json
{
  "timestamp": "1782950400000",
  "interval": "1s",
  "links": [{
    "groundLinks": [{
      "satelliteId": "940158",
      "groundStationId": "starlink-gs-0002-akita-japan"
    }],
    "satelliteLinks": [{
      "satelliteAId": "940001",
      "satelliteBId": "940002"
    }]
  }]
}
```

- `timestamp` is the start time as a millisecond string.
- `interval` is the sample interval, for example `1s`.
- `links[i]` contains all connections for sample `i`.
- `groundLinks` and `satelliteLinks` are empty arrays when that sample has no corresponding links.

## Examples

Generate S4 links:

```bash
python -m skyseed_v1.visualization.export_links \
  --planned-shell S4 \
  --start-timestamp 1782950400000 \
  --interval 1s \
  --steps 300 \
  --dry-run-json /path/to/fixed/links.json
```

Use `--planned-shell ALL` to include S1–S5. Add
`--post-url http://localhost:9091/api/v1/satellite/ground-links` to trigger a
backend read after the fixed file is written. With `--post-url`, the request
body is `{}`; link data remains in the agreed file and is not sent as the POST
body.

## Fixed inputs

- `planned_shell_orbit_data_s1_s5.json`: fixed S1–S5 planned-orbit records so both systems use identical satellite positions;
- `starlink_gateways.json`: default gateway catalog.

The exporter automatically reads both files from their canonical data locations.
