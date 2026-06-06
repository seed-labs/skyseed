# Network Lower-Layer Inventory

This note separates current satellite-specific research-platform logic from
lower-layer Linux/Docker/seedemu mechanics. It is a cleanup aid, not a migration
commitment: the listed items should stay functional in Skyseed until a
seedemu-side replacement exists.

## Keep In Skyseed

- `apps/build_iteration2_example_topology.py`
  - satellite constellation layout, gateway/UT inventory, slot inventory, and
    current small-scale experiment topology
- `topology_pipeline.py`
  - orbit, visibility, zone, and slice abstraction boundaries
- `experiment_topology.py`
  - current small-scale slice scenario generation and experiment intent
- `planner.py`
  - satellite/UT/gateway policy, route intent, label allocation policy, and
    slice-to-slice planning behavior
- `network_mutation.py`
  - conversion from slice/topology intent into host-side slot mutation intent

## Seedemu Integration Boundary

- `seed_integration.py`
  - current location for SEED/seedemu-facing helper code
  - owns mini Internet construction helpers, bootstrap-router creation,
    placeholder UT creation, satellite link creation, and compiler platform
    resolution
  - should remain thin; if a helper becomes a general seedemu capability, it is
    a candidate to move upstream instead of growing here
- `build_topology.py`
  - older broad topology builder retained for compatibility/reference
  - no longer used as the helper source for the current small-scale app

## Candidate Lower-Layer Migration Areas

- MPLS readiness during topology construction
  - current code: `seed_integration.enable_mpls_readiness()` and the older copy
    in `build_topology.py`
  - lower-layer reason: container MPLS sysctl setup is not satellite-specific
  - preferred future home: seedemu node/runtime option or a shared seedemu
    preflight helper
  - migration risk: must preserve per-container `net.mpls.platform_labels`
    behavior and host-kernel module assumptions
- Host MPLS preflight
  - current code:
    `test_bundle_current_network/scripts/run_full_runtime_validation.sh`
  - lower-layer reason: `modprobe mpls_router`, `mpls_iptunnel`, `mpls_gso`,
    and host `net.mpls.platform_labels` are Linux environment preparation
  - preferred future home: seedemu/host-runtime preflight layer
  - migration risk: failure messages must stay explicit; missing host MPLS is a
    common root-cause trap
- Node-local Linux command execution and batching
  - current code: `linux_mpls_runtime.py`
  - lower-layer reason: Docker exec fan-out, per-node batch execution, timing,
    command result collection, and rollback mechanics are runtime plumbing
  - preferred future home: seedemu runtime executor or shared execution toolkit
  - migration risk: batching and observation are part of the current successful
    validation path, so move only behind an equivalent interface
- Linux MPLS/IP/iptables command realization
  - current code: `linux_mpls_runtime.py` and lower-level builders in
    `mpls_control.py`
  - lower-layer reason: `ip -f mpls route`, `ip route`, `ip rule`,
    `iptables`, and `sysctl` command installation are Linux dataplane
    mechanics, not orbit/visibility policy
  - preferred future home: seedemu Linux dataplane realization layer
  - migration risk: Skyseed planner still owns desired forwarding intent; do
    not move route-policy decisions with the installer mechanics
- Docker network and slot mutation primitives
  - current code: `controller.py`, used through `network_runtime.py`
  - lower-layer reason: bridge creation/removal, Docker network
    connect/disconnect, interface rename/probe, and namespace-level mutation
    are physical runtime mechanics
  - preferred future home: seedemu dynamic-link runtime service or host daemon
  - migration risk: `link_state.json` persistence and slot semantics must stay
    observable to Skyseed planners/tests
- MPLS/kernel capability probing
  - current code: `controller.py` and `linux_mpls_runtime.py`
  - lower-layer reason: kernel module/sysctl/container capability probes are
    environment checks
  - preferred future home: seedemu preflight and runtime capability API
  - migration risk: checks must distinguish host failure from container failure

## Current Transitional Files

- `controller.py`
  - transitional host-side mutation boundary; keep until slot mutation is moved
    behind a smaller runtime manager
- `mpls_control.py`
  - transitional MPLS helper; split command realization mechanics from
    satellite-specific route/UT intent before migrating pieces
- `linux_mpls_runtime.py`
  - active real node-local runtime; candidate for extraction only after a
    stable executor interface exists
- `legacy_contract_adapters.py`
  - isolated old-controller-to-contract projection helpers; not part of the
    current canonical contract boundary

## Practical Rule

Skyseed should own:

- orbit and visibility inputs
- slice construction
- UT/gateway/satellite policy
- planner intent and validation expectations
- experiment scenarios and research-facing reports

Seedemu or a shared lower-layer runtime should own:

- host/container capability preparation
- Docker/namespace physical mutation
- Linux dataplane command application
- generic execution batching, probing, and rollback plumbing
