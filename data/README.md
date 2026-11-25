# Scene inputs

[中文](README.zh-CN.md)

| File | Contents | Consumer |
|---|---|---|
| [starlink/shells.yaml](starlink/shells.yaml) | Five compact circular-shell parameter presets | [S4 Scene construction](../examples/s4_handover/scene.py) |
| [starlink/ground_stations.yaml](starlink/ground_stations.yaml) | 355 individually located GS sites | Examples select sites before construction |
| [global_zones.yaml](global_zones.yaml) | Explicit 732-zone reference layout | Optional authored input; `Scene.create_default_zones()` generates the same partition directly |

## S1–S5 parameters

| Preset | Altitude (km) | Inclination (°) | Planes × satellites | RAAN (°) | Phase factor |
|---|---:|---:|---:|---|---:|
| S1 | 550 | 53 | 72 × 22 | Start 0, step 5 | 38.765 |
| S2 | 540 | 53.2 | 72 × 22 | Start 2.5, step 5 | 54.175 |
| S3 | 570 | 70 | 36 × 20 | Start 0, step 10 | 15.295 |
| S4 | 560 | 97.6 | 6 × 58 | Start 63.7, step 60 | 1 |
| S5 | 560 | 97.6 | 4 × 43 | 75.7, 87.7, 99.7, 117.7 | 2/15 |

Shapes, plane directions and the 2015-01-01 UTC epoch follow the reviewed
[FCC records](../research/starlink/gen1/shells/fcc-sat-mod-20200417-00037-gen1.json).
[Mapping](../research/starlink/gen1/shells/S1-S5-MAPPING.md) identifies the source
planes. S4 keeps source plane numbers 5–10; numbering does not change geometry.

Phase factors are **derived generation parameters**, not separately published FCC
Walker factors. They reproduce the rounded source anomalies within 0.05°;
generation retains full precision. First phase is zero except S5's 0.29°.
[Scene](../src/skyseed/scene/README.md#build-and-query) defines the formula and
construction arguments. Counts such as 6 × 59 or 6 × 60 are valid custom layouts.
There are no per-satellite records or link-capacity assumptions in this file;
connection budgets belong to the Link model.

The example reads a preset and calls `Scene.create_shell`; Scene does not parse
this YAML itself. Its saved `.scene` snapshot still contains the generated nodes,
groups and geometry, ready for consumers without regeneration. The raw research
archive remains separate and is not loaded at runtime.

## Ground-station evidence

GS coordinates come from the reviewed
[gateway evidence](../research/starlink/gen1/ground-stations/gateway-evidence-2026-08-25.json)
and [provenance](../research/starlink/gen1/ground-stations/PROVENANCE.md).
The retained inventory includes historical/planned sites and does not establish
current operation or hardware capacity. Verify it from the repository root:

```bash
python data/starlink/rebuild_frozen_ground_stations.py --check --research-source research/starlink/gen1/ground-stations/gateway-evidence-2026-08-25.json
```

`--write` explicitly rebuilds that file; ordinary examples only read it.
