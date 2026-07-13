# Visualization

Presentation-specific physical-link and export formats. Visualization consumes
logical/runtime evidence and is never authoritative for backend behavior except
where a profile explicitly freezes an input contract.

## External integration boundary

- `export_links.py` produces the stable combined `groundLinks` and
  `satelliteLinks` slice payload consumed by the visualization project.
- `export_ground_links.py` and `export_satellite_links.py` expose the two
  focused producers used by the combined exporter.
- `export_planned_shell_orbit_data.py` regenerates the shared deterministic
  orbit interchange file when that contract is deliberately updated.
- committed orbit and gateway inputs live under `data/orbits/` and
  `data/ground/`; generated link slices belong in an example's `output/`.
- `demo_globe.py` combines those contracts with UT policy and zone catalogs
  into a self-contained timeline page inside each run directory. It reads that
  run's already-frozen snapshots and physical links, validates source time and
  attachment/anchor identity, and performs no planning of its own.

These exporters consume topology data and never control backend or runtime
updates. Interface usage and schemas are documented in
`docs/interfaces/visualization/`.
