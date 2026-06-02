# OpenSN Investigation Report

Investigation date: June 2, 2026

This report records the current OpenSN investigation for `satellite/`
Iteration 2. It follows the local investigation rule that verified facts,
inferences, and open questions must be kept distinct.

## Sources Inspected

- OpenSN paper on arXiv:
  [OpenSN: An Open Source Library for Emulating LEO Satellite Networks](https://arxiv.org/abs/2507.03248)
- OpenSN repository:
  [OpenSN-Library/OpenSN-Library](https://github.com/OpenSN-Library/OpenSN-Library)
- Repository branch inspected:
  `lwh/dev` at commit `548b243355c61d62ef06d7cca53c018b1ec8ac00`
- Repository files inspected:
  - [readme.md](https://raw.githubusercontent.com/OpenSN-Library/OpenSN-Library/lwh/dev/readme.md)
  - [LICENSE](https://raw.githubusercontent.com/OpenSN-Library/OpenSN-Library/lwh/dev/LICENSE)
  - [daemon/main.go](https://raw.githubusercontent.com/OpenSN-Library/OpenSN-Library/lwh/dev/daemon/main.go)
  - [daemon/config/config.json](https://raw.githubusercontent.com/OpenSN-Library/OpenSN-Library/lwh/dev/daemon/config/config.json)
  - [TopoConfigurators/Standard/main.py](https://raw.githubusercontent.com/OpenSN-Library/OpenSN-Library/lwh/dev/TopoConfigurators/Standard/main.py)

## License

- Verified: the repository is under the `MIT` license, according to the
  repository `LICENSE` file.
- Verified: this report evaluates architecture ideas for inspiration and
  design review; it does not propose copying OpenSN code into Skyseed.

## 1. OpenSN Architecture Overview

- Verified: the paper presents OpenSN as a container-based LEO satellite
  network emulator intended to improve efficiency, scalability, and
  extensibility over prior Mininet-based and Docker-based emulators.
- Verified: the paper describes three main architectural components:
  `User-Defined Configurator`, `Container Network Manager`, and
  `Key-Value Database`.
- Verified: the paper says the `Key-Value Database` sits between
  user-defined control logic and runtime network management, instead of passing
  configuration directly from controller code to the network manager.
- Verified: the paper says the `Container Network Manager` contains at least
  `Container Runtime Manager`, `Virtual Link Manager`, and `Message Forwarder`.
- Verified: the repository README and daemon source show an executable called
  `NodeDaemon`, which is the runtime daemon started on each machine.
- Verified: `NodeDaemon` configuration distinguishes a leader/master node from
  follower/slave nodes through `is_servant` and `master_address`.

Inference:

- OpenSN’s architecture is closer to a distributed runtime manager with an
  external topology controller than to a monolithic single-process emulator.
- For Skyseed, the most important architectural idea is not "use OpenSN as-is"
  but "separate topology-control intent from node-local runtime execution and
  from low-level container/link management."

## 2. Runtime Update Workflow

- Verified: the README says OpenSN depends on three services:
  `Etcd`, `InfluxDB`, and `CodeServer`.
- Verified: the paper says user-defined topology control logic writes emulation
  parameters and rules through the `User-Defined Configurator`, and that this
  data is recorded in the `Key-Value Database`.
- Verified: the paper says `Message Forwarder` transfers information such as
  real-time topology, interface addresses, and user commands between the
  `Key-Value Database` and the multi-machine emulation environment.
- Verified: the paper says that in the regular emulation phase, the
  configurator synchronizes topology state from the runtime manager, computes
  real-time satellite positions, and configures handover plus link-delay
  updates.
- Verified: the example `TopoConfigurators/Standard/main.py` runs a loop that:
  - reads node and link state through the Python SDK
  - updates node positions
  - disables old links and enables new links for ground-station handover
  - updates link parameters such as connectivity, delay, bandwidth, and loss
  - writes per-instance configuration back into the system
- Verified: the README says node-local dynamic configuration data is updated
  into `/share/topo/${NODE_ID}`, and containers can run their own internal
  configuration programs to consume it.

Inference:

- The effective runtime update flow appears to be:
  - external controller computes desired runtime changes
  - desired state and metadata are stored in `Etcd`
  - node daemons and their submodules realize those changes locally
  - per-container configuration is exposed through mounted files
  - metrics go to `InfluxDB`
- The current OpenSN model looks more like "controller plus distributed daemon
  managers" than like "fully autonomous pull-only node agents."

## 3. Node Daemon Responsibilities

- Verified: `daemon/main.go` initializes the node and starts these modules:
  - `CreateInstanceManagerModule`
  - `CreateLinkManagerModule`
  - `CreateConfigWriteModule`
  - `CreateLinkParameterModule`
  - `CreateWebshellModule`
  - `CreateMasterNodeModuleTask` on non-servant nodes
  - `CreateMonitorModule` when monitoring is enabled
- Verified: `daemon/config/config.json` includes:
  - role selection through `is_servant`
  - cluster relationship through `master_address`
  - HTTP listener configuration through `listen_port`
  - local resource shaping through `instance_capacity`
  - task parallelism through `parallel_num`
  - monitoring cadence through `monitor_interval`
  - dependency endpoints including `etcd_addr`, `influxdb_addr`, and
    `docker_host_path`
- Verified: the paper says the `Container Runtime Manager` handles container
  creation, destruction, and single-node resource management.
- Verified: the paper says the `Virtual Link Manager` constructs virtual links
  for `ISL` and `GSL`.
- Verified: the README says the daemon starts with administrator privileges and
  interacts with Docker on the host.

Inference:

- OpenSN’s node daemon is not a narrow route installer. It is a broader machine
  runtime manager that owns containers, links, config propagation, monitoring,
  and optional interactive tooling.
- Skyseed should likely adopt the "node-local daemon exists" idea, but not the
  assumption that one daemon must own every concern from container lifecycle to
  web shell exposure.

## 4. Data / Config Distribution Model

- Verified: the paper says the `Key-Value Database` records machine, node,
  link, and application information.
- Verified: the paper’s table of key-value contents includes both required and
  user-defined fields.
- Verified: required machine-level fields include capacity and machine network
  identity.
- Verified: required node-level fields include node type, maintained links,
  machine placement, and geometric parameters.
- Verified: required link-level fields include address, link parameters, and
  link type.
- Verified: required application-level fields include launch timing and
  application metadata.
- Verified: the paper says user-defined configuration can also be passed to
  container applications.
- Verified: the README says container-local configuration is exposed through the
  mounted `/share` directory.
- Verified: the README says the Python SDK can control nodes, links, geographic
  information, and container configuration.

Inference:

- OpenSN’s KV layer is not just a narrow update queue. It is a shared
  repository for both declarative configuration and runtime state.
- The model mixes platform metadata, topology state, and application-level
  configuration in one data system.

Assessment for Skyseed:

- This is a strong reference for separating controller logic from runtime
  realization.
- It is a weaker fit if copied literally, because Skyseed wants clear
  abstraction boundaries among `TopologySnapshot`, planner outputs, runtime
  update transport, and node-local installers.

## 5. Topology Update Model

- Verified: the paper says the `User-Defined Configurator` specifies emulation
  parameters and rules such as constellation setup, connectivity, `ISL` failure
  model, `GSL` handover policy, and trajectory update behavior.
- Verified: the example controller computes positions in a loop, selects a new
  serving satellite for a ground station, disables old links, enables new
  links, updates per-node configuration, and adjusts link parameters.
- Verified: the paper says runtime topology state is synchronized from the
  runtime manager back to the configurator during regular emulation.
- Verified: the paper describes both topology-changing events such as `GSL`
  handover and parameter-changing events such as delay updates.

Inference:

- OpenSN’s topology update model is controller-driven and iterative.
- The example controller embeds specific policy decisions such as closest
  satellite selection and polar-region cross-link behavior. Those are examples,
  not necessarily mandatory OpenSN platform semantics.

Assessment for Skyseed:

- Skyseed should adopt the idea that topology control and runtime realization
  are separate.
- Skyseed should not adopt OpenSN’s example topology policy as platform
  architecture. Skyseed needs pluggable orbit, visibility, zone, and planning
  modules instead.

## 6. Link Update Optimization

- Verified: the paper says one major bottleneck in container-based emulation is
  interaction with Docker CLI.
- Verified: the paper says `Container Runtime Manager` replaces Docker CLI
  interaction with the official Docker SDK and uses distributed parallelism.
- Verified: the paper says `Virtual Link Manager` skips Docker Network Manager
  and directly manages Linux virtual devices and namespaces.
- Verified: the paper says this avoids unnecessary operations during link
  creation and improves link-operation parallelism.
- Verified: the paper says OpenSN uses `VXLAN` for inter-machine data transfer.
- Verified: the paper includes an `eBPF` link design:
  - intra-machine `eBPF` links can replace Linux bridge devices with redirect
    control
  - inter-machine `eBPF` links can avoid some of the overhead of legacy
    repackaging paths
  - handover can be implemented by updating redirect maps instead of deleting
    and recreating bridge devices
- Verified: the paper reports that OpenSN with `eBPF` links further improves
  `GSL` handover efficiency and can keep the same interface across handover.
- Verified: the paper reports successful emulation of a five-shell Starlink
  constellation with `4408` satellites and construction/link-update speedups
  over comparison systems.

Inference:

- The deepest OpenSN optimization idea is not "use eBPF everywhere" but
  "reduce expensive container-runtime and virtual-device churn, especially for
  frequent handovers."

Assessment for Skyseed:

- This is highly relevant to Iteration 2, especially the requirement to avoid
  excessive Docker CLI calls and unnecessary virtual link creation.
- The eBPF path is promising but should be treated as an advanced optimization
  option, not a first implementation requirement.

## 7. What Should Be Adopted In Skyseed

- Adopt the separation idea:
  - controller or planner logic should not directly be the low-level runtime
    manager
  - desired topology or runtime intent should flow through a clean contract
    boundary
- Adopt the node-local daemon or agent direction:
  - each machine or container-local runtime component should apply local state
    instead of relying on global shell mutation for every change
- Adopt the "avoid Docker CLI hot path" lesson:
  - prefer APIs, SDKs, or direct Linux control mechanisms over repeated
    process-spawned Docker CLI operations in runtime-critical paths
- Adopt the "reduce link churn" lesson:
  - model handover and link updates in a way that avoids destructive delete and
    recreate cycles when a lighter-weight update is possible
- Adopt the explicit capacity and parallelism mindset:
  - runtime architecture should expose concurrency and placement limits instead
    of assuming one-machine toy scale
- Adopt investigation-backed openness:
  - OpenSN’s use of a data store as an intermediate contract is a useful
    architectural reference, even if Skyseed later chooses a different backend

## 8. What Should Not Be Adopted In Skyseed

- Do not adopt `etcd` as a mandatory architecture decision just because OpenSN
  uses it.
  - Skyseed still needs a transport and storage abstraction first.
- Do not adopt OpenSN’s example topology controller policies as platform
  behavior.
  - Skyseed needs pluggable orbit, visibility, zone, topology slicing, and
    planning modules.
- Do not adopt a monolithic daemon scope without review.
  - OpenSN’s daemon appears to combine container lifecycle, link management,
    config writing, monitoring, and web shell support.
  - Skyseed may want a narrower local agent boundary.
- Do not define Skyseed planner output as "write mounted config files like
  OpenSN."
  - OpenSN’s mounted config-file mechanism is useful evidence, but Skyseed’s
    primary contract still needs to be a generic `RuntimeUpdatePlan`.
- Do not require `eBPF` in the first Iteration 2 implementation.
  - It is promising as an optimization target, but premature commitment would
    over-constrain the design.
- Do not copy OpenSN’s repository structure or internal module names blindly.
  - The reusable part is the architecture pattern, not the directory layout.

## 9. Open Questions

- Should Skyseed’s first runtime update transport be:
  - controller push
  - node pull
  - file or snapshot store
  - `etcd` adapter
  - another mechanism
- Should Skyseed’s node-local agent be per-container, per-host, or a hybrid
  model?
- Should Skyseed support both:
  - a generic action-plan contract
  - optional mounted per-node config payloads
  or only one of them at first?
- How much of OpenSN’s "shared KV for config and runtime state" should be
  mirrored in Skyseed, if any?
- If Skyseed later pursues link-update optimization, should it first explore:
  - direct Linux virtual-device control
  - stable-interface handover semantics
  - `eBPF`
  - or some smaller intermediate optimization
- What is the right boundary between:
  - topology snapshot generation
  - planner output generation
  - node-local installation
  so that OpenSN-inspired runtime efficiency does not collapse Skyseed’s
  mechanism and policy separation?

## 10. Recommended Next Design Step

- Write a dedicated Iteration 2 schema/design note for:
  - `TopologySnapshot`
  - `RuntimeUpdatePlan`
  - runtime update transport/store abstraction
  - node-local agent or installer contract
- Use the OpenSN investigation to shape evaluation criteria, not to lock the
  implementation.
- Specifically, the next design pass should compare at least three Skyseed
  runtime-update options:
  - minimal controller-push baseline
  - pluggable store plus polling or watch-based agent model
  - future optimization path for reduced link-churn and reduced Docker-runtime
    overhead
- Any option review should be scored against:
  - speed
  - stability
  - scalability toward large constellations
  - preservation of mechanism/policy separation
  - compatibility with replaceable planners
