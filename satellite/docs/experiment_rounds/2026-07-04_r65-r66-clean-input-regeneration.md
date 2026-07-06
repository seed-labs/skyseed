# r65/r66: Clean Input Regeneration After Corrupt Frozen Bundle

Date: 2026-07-04
Status: fixed; clean r66 frozen input ready for next runtime gate

## Goal

- Replace the stale r58 frozen prepared bundle after r65 stopped before runtime
  on JSON decode.
- Clean large known-bad artifacts that must not be reused.

## Findings

- r65 did not reach compose or BPF runtime. It stopped while loading
  `s4-full-h3-240s-prepare-20260704-r58/frozen_prepared_update_bundle.json`.
- The r58 `prepared_update_bundle.json` and
  `frozen_prepared_update_bundle.json` were hardlink aliases and both contained
  a non-JSON diff marker near `satellite_index_in^plane`.
- r62 and r63 prepared bundles still parse as JSON, but remain failed-run
  artifacts and are not inputs for the next runtime validation.

## Changes

- Deleted the corrupt r58 prepare directory.
- Deleted the old `cleanup-before-r56` preserved r53/r55 contaminated lineage.
- Regenerated a fresh prepare-only input with current r64 code:
  `satellite/output/validation_profiles/s4-full-h3-240s-prepare-20260704-r66`.
- Made the r66 prepared/frozen hardlink alias read-only to reduce accidental
  mutation risk while preserving disk space.

## Validation

- `frozen_prepared_update_bundle.json` parses as JSON.
- No r58, contaminated marker, or old non-JSON diff marker appears in the r66
  prepare directory.
- r66 pre-start/load gate passed without starting compose:
  `satellite/output/validation_profiles/s4-full-h3-240s-loadgate-20260704-r66`.
- Post-prepare preflight remained clean: no scoped containers, networks,
  direct-backend interfaces, BPF pins, or runtime locks.

## Remaining

- Next runtime should load:
  `satellite/output/validation_profiles/s4-full-h3-240s-prepare-20260704-r66/frozen_prepared_update_bundle.json`.
- r65 is only a failed input-load gate, not BPF clean-path evidence.
- Ping-through is still not proven by a recent BPF-owned run and should remain a
  separate bounded smoke after the performance validation.
