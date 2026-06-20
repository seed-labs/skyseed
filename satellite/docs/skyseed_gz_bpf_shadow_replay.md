# Skyseed G/Z BPF Shadow Replay

Goal 10 connects the Goal 9 G/Z FEC-to-BPF generation compiler to the Goal 8
isolated tc-BPF packet path. It is a shadow replay only: it does not change the
canonical runtime, does not change G/Z semantics, does not attach to the real
Skyseed dataplane, and does not run a long real-runtime experiment.

Artifacts:

- `satellite/apps/replay_gz_bpf_generation_shadow.py`
- `satellite/baselines/skyseed_gz_bpf_shadow_replay_summary.json`
- `satellite/experimental/bpf/skyseed_tc_gz_shadow.c`

The replay uses the existing 2h G/Z artifact by default:

```text
satellite/output/starlink_shell_experiments/gz-z6metadata-2h-gated-20260611-0824
```

No file under `satellite/output` is copied into the checked-in baseline.

## Scope

The runner rebuilds the full Goal 9 generation map from:

- `gz_fec_plan.json`
- `gz_update_diff_summary.json`
- `temporal_plan.json`, when present, for generation annotation only

Then it selects a short generation window and projects real G/Z FEC decisions
onto the small isolated topology used by the Goal 8 BPF shadow:

| replay slot | packet destination | isolated behavior |
| --- | --- | --- |
| `FEC_G` | `10.91.0.100` | generation-specific gw1-like, gw2-like, or inactive/drop |
| `FEC_Z` | `10.91.0.200` | local-delivery-like pass |
| missing FEC | `10.91.0.250` | fail-closed drop |

This projection is deliberate. The packet path proves that real artifact
generation decisions can drive BPF map state and active-generation switching in
an isolated environment; it does not claim that the isolated gw1/gw2 veths are
the real satellite/gateway dataplane.

## Generation Cadence

A generation is a version of forwarding state. It is not a fixed 10 second
slice, and this replay does not hard-code a 10 second cadence as long-term
semantics.

The current replay uses the discrete generation sequence already present in the
input artifact. RuntimeEpoch activation metadata is recorded when the artifact
contains it, but replay activation is controlled by BPF `active_generation`
updates during the isolated test.

The future target is event-time generation activation and a continuous-like
runtime where generation cadence comes from artifact/runtime policy rather than
from a fixed slice assumption.

## BPF Maps

The replay writes the same map shape as Goal 8:

| map | purpose |
| --- | --- |
| `active_generation` | selected generation slot |
| `generation_status` | installed-generation guard |
| `fec_forwarding` | projected `(generation, FEC)` decisions |
| `future_generation` | preloaded future generation audit slot |
| `packet_counters` | packet-path audit counters |

The tc-BPF shadow map capacities were increased to hold a small real generation
window without changing the packet-path semantics:

- `generation_status`: 32 entries
- `fec_forwarding`: 512 entries

## Validation

The default validation command is:

```bash
python satellite/apps/replay_gz_bpf_generation_shadow.py \
  --run-dir satellite/output/starlink_shell_experiments/gz-z6metadata-2h-gated-20260611-0824 \
  --generation-limit 3 \
  --format text
```

For the default artifact, the replay loads 3 generations from the compiled G/Z
generation map. Those generations contain 33 artifact FEC entries. The isolated
BPF projection writes 4 route entries: one `FEC_G` entry per replayed generation
plus one `FEC_Z` local-delivery entry.

The required packet-path probes are:

- active generation 0 `FEC_G` selects gw1-like next hop;
- generation 1 is preloaded as future state but does not affect generation 0;
- after activation, generation 1 `FEC_G` selects gw2-like next hop;
- generation 2 inactive tombstone drops;
- missing generation drops;
- missing FEC drops;
- active `FEC_Z` local-delivery path passes locally.

The current baseline reports all required checks passing:

- `generation_switch_ok`: true
- `fail_closed_ok`: true
- `local_delivery_ok`: true
- `residue.clean`: true

Counter summary from the current baseline:

```text
loaded_generation_count=3
loaded_fec_count=4
replayed_generation_count=3
artifact_fec_entry_count=33
future_generation_not_used=6
drop_missing_generation=2
drop_missing_fec=2
drop_inactive=2
redirect_pass=6
local_delivery=2
next_hop_gw1=2
next_hop_gw2=2
```

## Residue

The experiment uses temporary netns/veth names and pinned tc global maps only
inside the run. Cleanup removes:

- test namespaces;
- test veths;
- pinned BPF maps;
- the tc filter with the namespace qdisc.

The baseline records no owned namespace hits, no owned veth hits, no pinned map
residue, and no tc filter residue possibility after cleanup.

## Boundary To Future Work

This is ready to inform a ParameterEpoch BPF parameter-map prototype, but it is
not that prototype. This goal intentionally does not add qdisc timeline work,
does not land an optimized backend, and does not replace the canonical backend.
