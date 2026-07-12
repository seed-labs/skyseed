# Visualization Integration Contract

This directory documents the file boundary shared with the separately owned
visualization project. Skyseed owns deterministic orbit/ground inputs and link
slice production; the visualization system consumes those files and does not
participate in network planning or runtime activation.

Canonical inputs:

- `data/orbits/planned_shell_orbit_data_s1_s5.json`;
- `data/ground/starlink_gateways.json`;
- an experiment's generated `output/<run-id>/.../visualization_physical_links.json`.

The unified payload schema is documented in `export_links_EN.md`. Focused ground
and satellite exporters remain available for interface debugging. Changing
this boundary requires an explicit schema/version change and an integration
contract test; it must not silently change the runtime network semantics.
