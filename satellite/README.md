# Satellite Slot-Based Dynamic Link Prototype

Chinese version:
[README.zh.md](/home/seed/skyseed/satellite/README.zh.md:1)

This directory contains the current mainline satellite prototype for SEED
Emulator. The prototype is intentionally small and controller-driven:

- 3 `UT`
- 6 `ST` on two planes
- 3 `GW`
- plus a restored compile-time `B00 miniInternet`
- fixed intra-plane `ISL`
- slot-based runtime `UT-ST`, `GW-ST`, and cross-plane `ST-ST` links

The runtime control center is
[controller.py](/home/seed/skyseed/satellite/controller.py:1).
SEED creates the base containers, the fixed intra-plane links, and the
compile-time ground-side `miniInternet` structure, including `IX100-IX105`,
transit ASes, stub ASes, and the `GW` uplinks toward Internet exchanges.

Migration note for this first-stage `skyseed` mirror:

- when running from `/home/seed/skyseed`, interpret historical
  `examples/satellite/...` commands as `satellite/...`
- when running from `/home/seed/skyseed/satellite`, use the plain local script
  name such as `python3 controller.py ...`

## Current Phase Boundary

This phase currently does three tightly scoped things:

- restore the full compile-time `B00 miniInternet`
- keep the slot-based satellite runtime link model operational and inspectable
- add example/runtime-level `UT egress MPLS` for the currently attached
  supported `UT`s, with a minimal selected-`GW` `NAT/general return path`

This phase explicitly does not do the following:

- it does not restore the old `forwarding_state`, `candidate`, or `snapshot` workflows
- it does not introduce real distributed `MPLS` control-plane protocols such as `LDP`, `RSVP`, or `SR`
- the satellite runtime side does not run `OSPF/iBGP/eBGP`
- it does not provide session continuity across gateway/scenario changes
- it does not provide concurrent runtime transactions or multi-writer safety

So the current ground-side Internet, the runtime satellite link layer, and the
current `UT egress MPLS` dataplane now coexist inside one narrow
example/runtime workflow, without turning this prototype into a generic
mobility or concurrency design yet.

## Current Link Model

- `UT-ST`: each `UT` owns 2 access slots
- `GW-ST`: each `GW` owns 2 gateway slots
- cross-plane `ST-ST`: orbit-a and orbit-b share 3 reusable slots
- intra-plane `ST-ST`: fixed `front/back` links are compile-time created and always on

Each slot is one Docker bridge network. `UT` and `GW` fixed endpoints are
preconnected during `--init-slot-runtime`. `ST` endpoints are connected and
disconnected at runtime.
The `miniInternet` internal links and `GW` IX uplinks remain compile-time
structures and are not managed by the slot runtime.

## State Files

- `configs/topology.json`: static inventory with fixed links and slot inventory
- `configs/link_state.json`: runtime slot/link state owned by the controller

Older `attach_state.json`, `topology_state.json`, and `forwarding_state.json`
are no longer part of the active prototype workflow.

`link_state.json` remains limited to the satellite slot runtime plus `fixed_isl`
entries. It does not mirror the internal links of the ground-side
`miniInternet`.

## UT Address Semantics

The current prototype distinguishes two different address roles:

- `UT stable identity address/prefix`
  - `ut1 = 100.64.0.1/32`
  - `ut2 = 100.64.0.2/32`
  - `ut3 = 100.64.0.3/32`
  - these addresses live on `dummy0` and remain unchanged across `UT-ST` attach/detach
  - future forwarding/MPLS work should treat them as service endpoints, `FEC` targets, and user identity
- `UT-ST runtime slot link address`
  - for example `10.90.x.x/29`
  - these addresses describe only the current access-link next hop
  - slot-link IPs must not be treated as user identity addresses

## Build And Start

```bash
python3 satellite/build_topology.py
cd satellite/output
docker compose up -d
python3 ../controller.py --init-slot-runtime
```

## Minimal Validation

View current slot state:

```bash
python3 satellite/controller.py --show-link-state
```

Run the read-only `MPLS-0` capability probe:

```bash
python3 satellite/controller.py --probe-mpls
python3 satellite/controller.py --probe-mpls --probe-report satellite/configs/mpls_probe.json
```

The probe does not load host modules, does not change `sysctl`, and does not
install routes. It only checks:

- host-side `modprobe` readiness for `mpls_router` and `mpls_iptunnel`
- host-side visibility and non-zero value of `net.mpls.platform_labels`
- host-side `iproute2` support for `ip -f mpls route` and `encap mpls`
- currently running `ST/GW` containers for container-visible, non-zero `MPLS` sysctls and Docker capability posture

Run the minimal `MPLS-1` static label lab:

```bash
python3 examples/satellite/mpls_static_lab.py
```

Clean up the static `MPLS/IP` routes and interface `input` flags installed by
that lab:

```bash
python3 examples/satellite/mpls_static_lab.py --cleanup
```

Run the current `MPLS-2` controller-driven static LSP actions explicitly:

```bash
python3 examples/satellite/controller.py --install-static-mpls-lsp sat-a1:sat-b1:gw1
python3 examples/satellite/controller.py --verify-static-mpls-lsp sat-a1:sat-b1:gw1
python3 examples/satellite/controller.py --cleanup-static-mpls-lsp sat-a1:sat-b1:gw1
```

At this stage, `controller` owns explicit static `LSP` intent and validates
the current path against `link_state.json` before installing Linux `MPLS`
routes. `mpls_static_lab.py` remains as a regression/smoke wrapper and can
prepare the runtime path first when needed.

This `MPLS-1` lab currently validates only:

- `sat-a1 -> sat-b1 -> gw1`
- `push`
- `swap`
- `pop`
- Linux kernel `MPLS` dataplane / `LFIB` install / label switching / end-to-end forwarding

It explicitly does not yet do:

- dynamic path computation
- topology-driven `MPLS` orchestration
- handover-aware forwarding
- `LDP` / `RSVP` / `SR`
- a general `MPLS Layer/Service` API

Run the current `MPLS-3` single-UT Internet-egress controller actions:

```bash
python3 examples/satellite/controller.py --prepare-ut-egress-policy ut1
python3 examples/satellite/controller.py --install-ut-egress-mpls ut1
python3 examples/satellite/controller.py --verify-ut-egress-mpls ut1
python3 examples/satellite/controller.py --cleanup-ut-egress-mpls ut1
```

Where:

- `--prepare-ut-egress-policy` installs the one-time `static_guardrail` policy
  on every candidate serving satellite, including the source-rule skeleton and
  the aggregated internal/control/runtime guardrail prefixes; run it after
  `--init-slot-runtime`, and it is idempotent
- `--install-ut-egress-mpls` now installs only `dynamic_path` /
  `dynamic_lfib` / `dynamic_gateway_nat`, instead of reinstalling dozens of
  exclusion routes on every `t0/t1` switch
- `--dry-run` prints the plan plus category/node counts, while `--timing`
  prints timing summaries for static/dynamic/verify/cleanup execution

Run the repeatable `MPLS-3` regression/smoke wrapper for the current successful
path:

```bash
python3 examples/satellite/mpls_ut_egress_smoke.py
python3 examples/satellite/mpls_ut_egress_smoke.py --cleanup
```

Run the current multi-UT `MPLS-3/4` controller actions for all currently
attached supported UTs:

```bash
python3 examples/satellite/controller.py --install-all-attached-ut-egress-mpls
python3 examples/satellite/controller.py --verify-all-attached-ut-egress-mpls
python3 examples/satellite/controller.py --cleanup-all-attached-ut-egress-mpls
python3 examples/satellite/controller.py --verify-ut-egress-mpls ut1 --ping-target 10.160.0.71
```

Run the first `MPLS-4` scenario actions that reconcile runtime topology and
then verify `ut1` egress over the updated path without restarting containers:

```bash
python3 examples/satellite/controller.py --apply-scenario t0
python3 examples/satellite/controller.py --verify-scenario t0
python3 examples/satellite/controller.py --apply-scenario t1
python3 examples/satellite/controller.py --verify-scenario t1
```

Run the repeatable `MPLS-4` scenario smoke wrapper:

```bash
python3 examples/satellite/mpls_scenario_smoke.py --scenario t0
python3 examples/satellite/mpls_scenario_smoke.py --scenario t1
python3 examples/satellite/mpls_scenario_smoke.py --sequence t0,t1
python3 examples/satellite/mpls_scenario_smoke.py --scenario t0 --all-ut
python3 examples/satellite/mpls_scenario_smoke.py --scenario t1 --all-ut
```

Run the `MPLS-4` second-stage long-lived serial scenario loop:

```bash
python3 examples/satellite/mpls_scenario_loop.py
python3 examples/satellite/mpls_scenario_loop.py --rounds 4
python3 examples/satellite/mpls_scenario_loop.py --interval 5 --max-rounds 100
python3 examples/satellite/mpls_scenario_loop.py --sequence t0,t1 --rounds 0
python3 examples/satellite/mpls_scenario_loop.py --all-ut --max-rounds 2
```

At this stage the `controller` will:

- resolve `ut1`'s current serving satellite from `link_state.json`
- read the currently occupied `gw_access` slots
- choose the shortest-hop path across the active satellite graph
- convert that path into an explicit `UT egress MPLS intent/profile`
- program Linux `MPLS/IP` routes as a `default Internet egress` dataplane:
  the `UT` gets a default route toward its current serving satellite, while the
  serving satellite classifies `UT`-sourced non-local traffic into a dedicated
  source-policy table with controller-derived local/internal exclusions
- install minimal per-gateway Linux `iptables` `SNAT/FORWARD` rules so that the
  selected gateway can act as the current return point for miniInternet hosts

The current `MPLS-3` status is intentionally narrow:

- control-plane path selection / intent generation / label programming are implemented
- end-to-end `ut1(active access-link source/interface) -> 10.154.0.129` ping now succeeds over the controller-managed `MPLS` path
- `ut2(active access-link source/interface) -> 10.154.0.129` now also succeeds
  over the controller-managed `MPLS` path in the currently attached scenarios
- at least one arbitrary miniInternet host beyond `10.154.0.129` is now
  validated over the same `MPLS + GW NAT` dataplane; the current all-UT smoke
  path exercises `ut1`, `ut2`, and `ut3` against `10.160.0.71`
- `UT stable identity` (`100.64.0.1/32 on dummy0`) remains a controller/FEC identity, not the current Linux business-source address
- `UT` egress no longer depends on an enumerated external host-prefix allowlist
  for its core forwarding semantics; the current controller derives
  local/internal exclusion prefixes from `topology.json`, then treats the rest
  of `UT`-sourced traffic as default Internet egress toward the selected
  gateway
- host `bridge netfilter` passthrough is now part of runtime readiness: `--init-slot-runtime` prepares `net.bridge.bridge-nf-call-{iptables,ip6tables,arptables}=0`, and `UT egress MPLS` install/verify will fail explicitly if those sysctls are not `0`
- `mpls_ut_egress_smoke.py` keeps the current regression path fixed at `ut1 -> sat-a1 -> sat-b1 -> gw1 -> 10.154.0.129`, verifies `access-link -> push -> swap -> pop -> round-trip`, then cleans forwarding state while leaving runtime topology attached
- `--install-all-attached-ut-egress-mpls` / `--verify-all-attached-ut-egress-mpls`
  / `--cleanup-all-attached-ut-egress-mpls` now operate on every currently
  attached supported UT intent in `link_state.json`
- the current all-UT scope is still intentionally small:
  the default runtime validation path centers on `t0/t1` and the outbound
  Internet-egress baseline only
- the current return path is now a minimal selected-GW NAT model, not a full
  mobility-control design:
  the selected gateway installs `SNAT` to its bootstrap-side miniInternet IP
  (`10.65.21.2`, `10.65.22.2`, or `10.65.23.2`) and relies on Linux
  conntrack/NAT reverse mapping for replies from arbitrary miniInternet hosts
- active session continuity across `t0/t1` gateway changes is still not
  guaranteed; ongoing flows may break when the selected gateway changes
- `controller.py --apply-scenario t0|t1` now reconciles one explicit runtime scenario at a time, using only serial attach/release/connect actions and validating the resulting `link_state.json`
- `mpls_scenario_smoke.py --sequence t0,t1` now proves that `t0 -> t1` can be applied in-place, with forwarding cleaned between steps and `ut1` egress `MPLS` reinstalled on the new shortest path
- `mpls_scenario_smoke.py --scenario t0|t1 --all-ut` now proves the same for
  all currently attached supported UTs and additionally exercises an arbitrary
  miniInternet host target (`10.160.0.71`) after the default per-UT verify
- real runtime validation now covers `ut1`, `ut2`, and `ut3` over the current
  `default Internet egress` dataplane in actual containers, including a strict
  `cleanup all -> apply scenario -> reinstall all -> verify all` cycle
- single-UT forwarding cleanup now preserves shared `MPLS input` interfaces
  when other installed `UT` egress plans still depend on the same satellite or
  gateway ingress interface
- current serial runtime timings are now recorded from real container runs and
  already show why future parallel runtime/controller update work will matter:
  `apply_scenario t1` was measured at `44.78s`, while one strict all-UT
  `t1 -> cleanup all -> t0 -> install all -> verify all` pass measured
  `cleanup_all=11.35s`, `apply_scenario t0=48.51s`, `install_all=39.98s`, and
  `verify_all=16.17s`
- `mpls_scenario_loop.py` now repeats that same serial scenario cycle as a loop runner, printing the current scenario, selected gateway, selected path, and labels on every successful round
- running `python3 examples/satellite/mpls_scenario_loop.py` now starts the continuous demo by default: it loops forever, sleeps 5 seconds between rounds, and may be bounded with `--max-rounds`
- `mpls_scenario_loop.py --all-ut` exists for explicit all-UT demo runs, but
  the longest repeated-transition soak evidence still centers on the default
  ut1-only loop; use the all-UT smoke wrapper first when validating changes
- each loop round still runs `cleanup forwarding -> apply scenario -> verify scenario -> install ut1 egress MPLS -> verify dataplane -> cleanup forwarding`
- on `Ctrl-C`, the loop runner performs best-effort forwarding cleanup, prints the final `link_state`, and exits; if the interrupt lands in the middle of an in-flight runtime mutation, rerun `--apply-scenario` or the smoke wrapper once to reconcile topology explicitly
- on any attach/release/connect/install/verify failure, the loop runner aborts the current round, performs best-effort forwarding cleanup, prints `link_state`, and exits instead of continuing with uncertain state
- the loop runner now also does a first residue/stability pass after every successful round:
  it checks forwarding cleanup, scenario/link-state consistency, bridge occupancy for managed slot networks, and stale satellite runtime interfaces; any anomaly stops the loop
- current bounded soak evidence is now available at the example/runtime layer:
  a 10-round run completed successfully, and a longer 50-round target was observed through 44 consecutive successful rounds before an operator stop, with no stale `LFIB`, stale IP route, `link_state` mismatch, or stale satellite runtime interface detected in the completed rounds
- if `Ctrl-C` lands in the middle of an in-flight runtime mutation, the forwarding cleanup is still best-effort only and the topology may remain in a partial transition state; explicitly rerun `--apply-scenario` or the smoke wrapper once before continuing
- this is now the first continuous-loop demo/stability implementation step, but it is still single-UT only and not a background topology watcher
- controller state mutation remains serial-only; do not run `--attach-ut`, `--attach-gw`, `--connect-cross`, or `--release-slot` in parallel
- the current runtime controller should be treated as `single-writer / serialized-operation safe only`; do not run multiple controller terminals that mutate runtime state at the same time

Current correctness baseline and recommended manual acceptance commands:

```bash
# all-UT external egress in the current attached scenario
python3 examples/satellite/controller.py --install-all-attached-ut-egress-mpls
python3 examples/satellite/controller.py --verify-all-attached-ut-egress-mpls
```

The current default correctness baseline is the residential / `CGNAT-like`
`UT -> external` path only:

- `UT -> serving satellite -> selected GW -> miniInternet host`
- the selected `GW` applies the current minimal `SNAT/FORWARD` realization for
  outbound access and return traffic
- `external host -> UT` is not part of the baseline
- `UT -> UT` direct reachability is not part of the baseline
- if future `UT <-> UT` support is revisited, it should be designed as a
  separate operator-internal subscriber-routing/core problem rather than as a
  public-inbound or per-pair shortcut extension

Recommended manual acceptance commands remain:

```bash
python3 examples/satellite/controller.py --prepare-ut-egress-policy ut1 --timing
python3 examples/satellite/controller.py --apply-scenario t0
python3 examples/satellite/controller.py --install-ut-egress-mpls ut1 --timing
python3 examples/satellite/controller.py --verify-ut-egress-mpls ut1 --timing

python3 examples/satellite/controller.py --cleanup-ut-egress-mpls ut1 --timing
python3 examples/satellite/controller.py --apply-scenario t1
python3 examples/satellite/controller.py --install-ut-egress-mpls ut1 --timing
python3 examples/satellite/controller.py --verify-ut-egress-mpls ut1 --timing

python3 examples/satellite/controller.py --install-all-attached-ut-egress-mpls
python3 examples/satellite/controller.py --verify-all-attached-ut-egress-mpls
```

The practical transition goal is now limited to `t0 <-> t1`:

- `t0` validates outbound egress through the current selected gateway/path
- `t1` validates that the controller can switch to the new selected gateway/path
- active pings may transiently drop during the serial runtime mutation, but the
  dataplane should recover once the new forwarding state is installed

Count Docker bridge networks created for the prototype:

```bash
docker network ls --format '{{.Name}}' | grep -E '(access-slot|gateway-slot|cross-slot|^link-a|^link-b)'
```

Count interfaces inside one container:

```bash
docker exec ut1 ip -o link show | wc -l
docker exec gw1 ip -o link show | wc -l
docker exec sat-a1 ip -o link show | wc -l
```

Trigger one `UT-ST` dynamic attach:

```bash
python3 examples/satellite/controller.py --attach-ut ut1:sat-a1
python3 examples/satellite/controller.py --attach-ut ut1:sat-a1 --slot-name ut1-access-slot1
```

Release that `UT` slot:

```bash
python3 examples/satellite/controller.py --detach-ut ut1
```

Trigger one `GW-ST` dynamic attach:

```bash
python3 examples/satellite/controller.py --attach-gw gw1:sat-b1
python3 examples/satellite/controller.py --attach-gw gw1:sat-b1 --slot-name gw1-gateway-slot1
```

Release that `GW` slot:

```bash
python3 examples/satellite/controller.py --detach-gw gw1
```

Trigger one cross-plane dynamic connection:

```bash
python3 examples/satellite/controller.py --connect-cross sat-a1:sat-b1:left:right
python3 examples/satellite/controller.py --connect-cross sat-a1:sat-b1:left:right --slot-name ab-cross-slot2
```

Release that cross-plane slot:

```bash
python3 examples/satellite/controller.py --release-slot ab-cross-slot0
```

Inspect one container after runtime attach:

```bash
docker exec sat-a1 ip -o addr show
docker exec ut1 ip -o addr show
docker exec gw1 ip -o addr show
```

## Runtime Demo

A serial handover demo script is available at:

`examples/satellite/demo_runtime_handover.py`

Example:

```bash
python3 examples/satellite/demo_runtime_handover.py --interval 5 --iterations 2
```

The demo alternates between:

- `t0`: `ut1 -> sat-a1`, `gw1 -> sat-b1`, cross-plane `sat-a1 <-> sat-b1`
- `t1`: release cross-plane, detach `ut1`, detach `gw1`, then `ut1 -> sat-a2`, `gw1 -> sat-b2`, cross-plane `sat-a2 <-> sat-b2`

After each serial runtime operation, the script prints:

- current active links
- current occupied slots
- the runtime operation being executed
- a `link_state.json` summary
- simple reachability probes for the current `UT`, `GW`, and cross-plane access IPs

The demo intentionally reuses the same slot names:

- `ut1-access-slot0`
- `gw1-gateway-slot0`
- `ab-cross-slot0`

So the probe IPs are slot-peer IPs, not satellite-specific IPs. During
handover, the `binding` and container interfaces change while the probe IPs
remain the same.

This current demo only validates slot-runtime attach/detach/connect/release,
slot reuse, state consistency, and visualization/inspection workflow. It does
not exercise the newer `UT egress MPLS` or selected-`GW` `NAT/general return`
workflow; it also does not restore the old forwarding baseline.

## Safety Rules Implemented In Controller

- after each `docker network connect`, the target container must gain exactly 1 new interface
- the controller only renames the discovered new interface
- if validation fails, the controller rolls back the partial runtime connect
- `link_state.json` is written only after runtime operations succeed
- cross-plane connect uses two-step connect with rollback if the second `ST` fails
- if `--slot-name` is not provided, the controller uses the first free slot in sorted slot-name order

## Bootstrap Interface Note

- some containers may still show `eth*` interfaces left from the SEED bootstrap stage in a `DOWN` state
- this is not a slot-runtime bug by itself
- slot validation is based on the interface diff immediately before and after `docker network connect`
- the controller cares about “exactly one newly appeared interface”, not about removing every old bootstrap-era `eth*`

## Routing Boundary

- the restored `miniInternet` keeps SEED-managed `Routing + Ebgp + Ibgp + Ospf`
- `GW` ground-side IX uplinks keep SEED-managed `eBGP` semantics
- `AS250` and the satellite-runtime side of `AS65000` are explicitly masked from SEED `Ospf/Ibgp`
- `UT-ST`, `GW-ST`, cross-plane `ST-ST`, and the current fixed intra-plane `ISL` remain controller-owned runtime topology semantics rather than SEED-managed satellite forwarding

## Observation Tips

- `SEED map` URL: `http://127.0.0.1:8080/pro/map`
- treat the current map as a compile-time topology view produced by the Docker compiler
- in the current version, that compile-time view should include the full restored `B00 miniInternet`, the `AS250` satellite side, and the `AS65000` gateways
- do not assume the map will reflect runtime `docker network connect/disconnect` slot changes in real time
- use the demo script output plus `python3 examples/satellite/controller.py --show-link-state` for runtime truth
- this phase does not provide a stable `UT -> ground host` ping target because forwarding/MPLS is intentionally out of scope
- this demo reuses the same `UT` access slot, so the recommended `UT`-side observation is to keep probing the same slot-peer IP while watching `binding.satellite_id` change:

```bash
while true; do
  date
  docker exec ut1 sh -lc 'ping -c 1 -W 1 10.90.11.1 >/dev/null 2>&1 && echo ut1-slot0 up || echo ut1-slot0 down'
  python3 examples/satellite/controller.py --show-link-state | grep 'ut1-access-slot0'
  sleep 1
done
```

- for `GW` feeder handover, keep probing the same slot-peer IP and watch `binding.satellite_id`:

```bash
while true; do
  date
  docker exec gw1 sh -lc 'ping -c 1 -W 1 10.91.11.1 >/dev/null 2>&1 && echo gw1-slot0 up || echo gw1-slot0 down'
  python3 examples/satellite/controller.py --show-link-state | grep 'gw1-gateway-slot0'
  sleep 1
done
```

- for cross-plane handover, watch the same cross-plane peer IP from `sat-a1` or `sat-a2` and pair it with the `ab-cross-slot0` binding:

```bash
while true; do
  date
  docker exec sat-a1 sh -lc 'ping -c 1 -W 1 10.92.1.2 >/dev/null 2>&1 && echo cross-slot0-from-a1 up || echo cross-slot0-from-a1 down'
  docker exec sat-a2 sh -lc 'ping -c 1 -W 1 10.92.1.2 >/dev/null 2>&1 && echo cross-slot0-from-a2 up || echo cross-slot0-from-a2 down'
  python3 examples/satellite/controller.py --show-link-state | grep 'ab-cross-slot0'
  sleep 1
done
```

## MPLS-0 Boundary

The first step of the next stage is a capability probe only.

- it does not restore the old fake `forwarding_state`
- it does not install kernel `MPLS` routes yet
- it does not connect `UT -> miniInternet`
- it does not introduce `LDP`, `RSVP`, or distributed label signaling

The purpose of `MPLS-0` is to answer one narrow question first:

- is the current host plus Docker runtime able to support the planned centralized Linux kernel `MPLS` dataplane

## Future Forwarding/MPLS Demo Plan

The following state design is the planned future loop for the next
forwarding/MPLS baseline stage. It is a plan only; it does not mean that
`UT -> miniInternet` reachability is implemented in the current phase.

`t0`:

- `ut1 -> sat-a1`
- `ut2 -> sat-a2`
- `gw1 -> sat-b1`
- `gw2 -> sat-a3`
- `ab-cross-slot0: sat-a1(left) <-> sat-b1(right)`
- `ab-cross-slot1: sat-a2(left) <-> sat-b2(right)`

`t1`:

- `ut1 -> sat-a2`
- `ut2 -> sat-a3`
- `gw1 release`
- `gw2 -> sat-b2`
- `ab-cross-slot0: sat-a2(left) <-> sat-b2(right)`
- `ab-cross-slot1: sat-a3(left) <-> sat-b3(right)`

The next forwarding/MPLS stage is expected to make the `UT stable identity
address/prefix` stay reachable across these runtime link-state changes while
using the slot-link addresses only as current next-hop information.

- the observation focus is not “IP changes from A to B”. It is:
  - ping may briefly fail during release/attach/connect
  - the same slot-peer IP becomes reachable again after handover
  - `link_state.json` binding moves from `sat-a1/sat-b1` to `sat-a2/sat-b2`

## Design Note

Detailed design notes are in
[docs/satellite_phase1_design.md](/home/seed/skyseed/satellite/docs/satellite_phase1_design.md:1).
Although the file name is historical, the document now describes the current
Phase 2 slot-based prototype and supersedes the old candidate-link mainline.

MPLS core-integration notes are tracked in
the original `seed-emulator` development tree; they were not copied into this
first-stage `skyseed` mirror.
