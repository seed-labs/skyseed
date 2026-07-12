# r108: visualization physical-link contract

Date: 2026-07-09
Status: fixed

## Goal

- Connect the active clean S4 profile to visualization-sourced orbit, gateway
  coordinates, satellite/GW visibility, and ISL connectivity without restoring
  old catalog-order, smoke, fallback, or hidden-repair paths.
- Keep UT attachment as Skyseed's own access semantics: UTs are workload probes
  that compute visibility to overhead satellites, not inputs that mutate the
  satellite network core.

## Findings

- The active profile still selected gateways by `catalog-derived-first-n`,
  which let the incidental order of the 250-entry visualization gateway file
  assign `gw201..gw212` policy anchors.
- Real-coordinate preview with the first serviceability pass showed two active
  required zones were not serviceable in the current S4/348/time-window setup:
  `zone-africa-east-optional` and `zone-south-pacific-observation`.
- Those zones are excluded from the current active profile as a temporary
  current-constellation serviceability choice, not a permanent zone semantics
  rule. Longer-term zone generation should derive zones from satellite
  coverage/serviceability before selecting gateways and inserting UT probes.

## Changes

- Added an explicit 12-GW binding catalog from Skyseed IDs to real
  visualization gateway IDs. Loading a visualization gateway catalog without
  that binding now fails closed.
- The active profile now selects 28 UTs, 14 serviceable zones, and 12 bound GWs
  explicitly. It uses real catalog coordinates, not shell-aware relocation.
- Preview/freeze now writes `visualization_physical_links.json`,
  `visualization_ground_stations.json`, and
  `visualization_serviceability_report.json` before topology snapshots.
- Snapshot visibility uses visualization frozen links for GW access and ISL.
  UT access uses Skyseed geometry and is labeled
  `skyseed_ut_access_geometry_v1`.
- Runtime still consumes prepared topology artifacts only; visualization
  exporters are not called from live epoch updates.

## Validation

- Focused unit tests cover explicit gateway binding, profile command expansion,
  and the frozen visualization visibility model boundary.
- Broader profile/session tests pass: 267 tests.
- Profile dry-run passes with `--gateway-binding-catalog`, `--zone-ids`,
  `--require-visualization-links`, and fixed visualization timestamp present.
- Real-coordinate preview-only gate passed with:
  - 28 selected UTs, 14 selected zones, 12 selected GWs
  - visualization serviceability `true`
  - 96 frozen link frames
  - 7,963 GW ground links
  - 42,504 satellite links
  - snapshot visibility model `visualization_frozen_physical_links_v1`
  - unresolved active zone count `0`

## Rerun Gate

- Previous failed/invalid risk:
  file-order gateway selection, hidden profile ambiguity, and unserviceable
  active zones would have wasted a real run after compose startup.
- Cheap check now catching it:
  profile contract tests, profile dry-run, and preview-only visualization
  serviceability/snapshot gate.
- Forbidden-work expectations:
  no catalog-order GW assignment, no runtime visualization generation, no
  fallback/full reload/full scan/live diff, no smoke/capped launch path.

## Remaining

- This is a current-profile serviceability selection. A later refactor should
  replace the static 14-zone selection with a generator that derives zones from
  satellite coverage first, then selects gateways, then inserts UT probes.
