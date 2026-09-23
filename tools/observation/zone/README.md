# Zone Tool

Browse the static Zone layout on globe/map views. Select a region to inspect its
identifier and declared boundaries; colors distinguish regions. Display outlines
are sampled geography, not a replacement for the membership Model. There is no
calculation time cursor because the layout is static.

To generate/check a layout, use `python -m tools.observation.zone.generate --output zones.yaml`
or `python -m tools.observation.zone.generate --check data/global_zones.yaml`. Band count,
equatorial cell count and alignment options are shown by `--help`. Normal examples
load their chosen layout without regenerating it.

[Open the interactive Zone demo](../../../docs/demos/s5/zone/index.html).
Its [provenance](../../../docs/demos/README.md) identifies the scenario, interval and
historical evidence; it does not validate current live execution.

To browse your own output, run a maintained example with Tools enabled and open
`output/visualizations/index.html` after a completed request. `--operation generate`
selects finite generation. Runner/Traffic views additionally require actual execution
records. See [outputs and Tools](../../../docs/user-guides/outputs-and-tools.md).
