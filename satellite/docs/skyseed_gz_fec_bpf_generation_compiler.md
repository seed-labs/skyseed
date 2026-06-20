# Skyseed G/Z FEC To BPF Generation Map Compiler

Goal 9 adds a shadow compiler from existing Skyseed G/Z FEC artifacts to the
Goal 8 BPF generation-map shape. It does not change G/Z semantics, does not
change the canonical runtime, does not attach BPF to the real dataplane, and
does not generate new long-runtime artifacts.

Artifacts:

- `satellite/apps/compile_gz_fec_to_bpf_generation.py`
- `satellite/baselines/skyseed_gz_fec_bpf_generation_summary.json`
- `satellite/test_gz_fec_bpf_generation_compiler.py`

## Input

The baseline was compiled from the existing 2h canonical run:

```text
satellite/output/starlink_shell_experiments/gz-z6metadata-2h-gated-20260611-0824
```

The compiler reads:

- `gz_fec_plan.json`
- `gz_update_diff_summary.json`
- `temporal_plan.json`, when available, only to annotate matching RuntimeEpoch
  IDs and activation times.

No file under `satellite/output` is copied into the repository baseline.

## BPF Generation Map Shape

The compiler emits a backend-neutral-ish JSON view of the Goal 8 maps:

| map | compiled representation |
| --- | --- |
| `active_generation` | slot `0` contains the selected generation ID |
| `generation_status` | one installed/active record per compiled generation |
| `fec_forwarding` | one record per `(generation, fec_key_u32)` decision |
| `packet_counters` | expected counter labels for audit/replay |

Each FEC forwarding entry carries:

- generation ID and epoch ID;
- canonical string FEC ID and stable `u32` FEC key;
- FEC kind: `G` for gateway-exit FECs, `GZ` for gateway-zone return FECs;
- selected gateway, zone, and responsible satellite;
- logical next-hop node/edge;
- active/inactive state;
- local-delivery marker when the GZ responsible satellite is the path terminal;
- expected counter labels.

Removed FECs from `gz_update_diff_summary.json` are compiled as inactive
tombstones in the target generation. This gives the BPF shadow model an
explicit `active=false` record and makes inactive FEC behavior auditable as
`drop_inactive`; it does not change canonical G/Z intent.

The full in-memory compiler output contains the full `fec_forwarding` table.
The checked-in baseline is compact: it stores generation summaries, map schema,
entry counts, representative G/GZ/inactive samples, and self-check probes. Use
`--full-json-out` to write the full generation map JSON when needed.

## Result

For the 2h G/Z run, the compact baseline reports:

- generations: `25`
- active FEC entries: `250`
- inactive tombstones from removed FEC groups: `18`
- total FEC map entries: `268`
- FEC kind counts: `125` `G`, `125` `GZ`
- generations matched to RuntimeEpoch metadata: `12`

The mismatch between 25 G/Z plan snapshots and 12 RuntimeEpoch annotations is
expected for this artifact set: the compiler preserves every G/Z FEC plan
generation and annotates RuntimeEpoch IDs only where `temporal_plan.json`
exposes a matching runtime epoch for the slice.

The `generation` records in this baseline come from the existing 2h artifact's
G/Z FEC plan/diff sequence; they do not require future backends to pin
generation cadence to a fixed 10s slice. Slice and cadence should remain
parameterized, and an optimized backend should support event-time generation
activation rather than binding itself to fixed 10s updates.

## Activation And Fail-Closed Checks

The compiler includes static lookup self-checks that mirror the Goal 8 shadow
model:

- a preloaded future generation does not become `active_generation`;
- missing generation maps to `drop_missing_generation`;
- missing FEC maps to `drop_missing_fec`;
- inactive tombstone maps to `drop_inactive`.

These checks are pure JSON/model checks. They do not attach BPF and do not
redirect packets.

## Linux/MPLS Leakage Audit

The current G/Z artifacts still contain Linux/MPLS realization fields:

- `forward_labels`
- `reverse_labels`
- `estimated_lfib_route_operation_items`

The compiler records these under `linux_mpls_label_leakage` and ignores them
when deriving BPF intent. The BPF generation map is driven by semantic FEC
grouping, selected gateway, zone, responsible satellite, and logical next-hop
fields, not MPLS labels.

## Commands

Compile the compact baseline:

```bash
python satellite/apps/compile_gz_fec_to_bpf_generation.py --format text
```

Optionally emit the full generation table:

```bash
python satellite/apps/compile_gz_fec_to_bpf_generation.py \
  --full-json-out /tmp/skyseed_gz_fec_bpf_generation_full.json
```

Verification:

```bash
python -m py_compile satellite/apps/compile_gz_fec_to_bpf_generation.py
python -m unittest satellite/test_gz_fec_bpf_generation_compiler.py
git diff --check
```
