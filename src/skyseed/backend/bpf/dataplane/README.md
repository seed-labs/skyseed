# C/eBPF DataPlane

`program.py` exposes `BpfProgram` and prepares C/ELF inputs during calculation.
`include/skyseed.h` defines the public handler contract and shared lifecycle/observation
support; `default.bpf.c` implements the optional default forwarding-table policy.
`translation.py` expands intentions into Linux actions, encoded node-version patches
and retirement bindings. The existing material writer invokes these before sealing.

`materials.py` validates the sealed foundation, mechanically replays patches and
owns the exact BPF capacity schema, bounds and incremental demand accounting.
Sealing and decoding select these BPF rules explicitly; Linux owns its own demand.
`object_contract.py` inspects declared ELF contracts and binds runtime-owned maps
through libbpf. It never compiles or analyzes user algorithms. `runtime.py` is Runner's
owned adapter; `runtime_command.py` executes its privileged work and retains object
ownership, including private mutable maps. `telemetry_abi.py` owns packet decoding.

[Linux mechanisms](../../linux/README.md) provide libbpf objects, syscalls, batch writes
and netlink. This package adds no coordinator. See the authoritative
[C/object contract](../../../../../docs/development/dataplane.md),
[delivery contract](../../../../../docs/development/compiler-and-runner.md) and
[copyable example](../../../../../templates/dataplane/README.md).

`data.py` owns explicit record definitions, generated C headers and encoding.
`scopes.py` owns public node-level program selection. `translation.py` preserves
local values and emits per-node profile table deltas; `materials.py` replays sealed
bytes. Runtime code binds and reclaims managed tables while keeping object-owned
mutable maps alive. The [contract](../../../../../docs/development/dataplane.md)
and [walkthrough](../../../../../templates/dataplane/threshold/README.md) are the
public entry points.

`local_ingress.py` owns fixed local injection setup/cleanup under the existing
Runner adapter. Its pair is infrastructure, outside Plan connections and network
port generations. The selected node object handles both local and network ingress.
