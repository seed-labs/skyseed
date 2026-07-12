# Directory Growth Audit

This audit applies the v1 rule that a concept likely to own implementation,
configuration, fixtures, variants, or documentation should become a
self-contained directory with an explicit public boundary. It does not itself
authorize another bulk move.

## Current result

The example, test, backend-family, data, documentation, and native-source
boundaries are directory based. `examples/s4_operational_clean/` and
`examples/s5_operational_clean/` use the reference layout: `run.py`, one
editable `experiment.py`, README, and ignored local `output/`. Every top-level
source responsibility now has a local README, and the cheap test tree has a
direct `tests/run.py` entry plus tier directories.

The remaining crowding risk is concentrated in a few very large orchestration
modules rather than in broad flat directory listings:

| Current owner | Approximate size | Eventual package boundary |
|---|---:|---|
| `runtime/experiment.py` | 1.55 MB | lifecycle runner, timing, progress, artifacts, cleanup, acceptance projections |
| `backends/linux/mpls.py` | 651 KB | planning/specs, apply, validation, session, BPF-owned bridge contract |
| `backends/linux/network.py` | 252 KB | inventory, slot lifecycle, qdisc, rollback |
| `runtime/profile.py` | 245 KB | resolved-profile launch, cleanup, and derived acceptance audit |
| `backends/bpf/live.py` | 162 KB | maps, attach lifecycle, counters, cleanup |
| `planning/temporal.py` | 147 KB | event model, horizon planning, serialization |

These are candidates for later module-to-package splits. Each split must first
lock public imports, serialized artifacts, failure labels, and the successful
call route with focused tests. Moving all of them together would recreate the
risk that the structural cutover was designed to control.

## Required shape for future splits

- package `README_EN.md`/`README_ZH.md`: responsibility, public imports, and
  forbidden dependencies in both maintained documentation languages;
- `__init__.py`: deliberately small public surface;
- separate contract/model code from execution code;
- fixtures and expected evidence under the owning test/example directory, not
  production source;
- generated artifacts under the owning example's ignored `output/`, or under
  an explicitly selected external run root for automation;
- one concept per directory, without one-file nesting used only for symmetry.

Priority should follow measured change pressure and bug density. The first
candidate is `runtime/experiment.py`, but only as small extraction stages (for
example console/progress, timing derivation, then artifact projection), each
with equivalence tests before the next extraction.
