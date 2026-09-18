# System architecture

SkySEED turns an authored satellite scene into time-indexed physical facts, planned
network states and executable BPF materials. Execution then produces observed network
and Traffic records. The complete diagram follows that path and shows the objects
and resources that each owner retains.

[**Open the full-size, zoomable system diagram**](system-architecture.svg) ·
[Editable diagram source](system_architecture.py)

![Complete SkySEED system architecture](system-architecture.svg)

The diagram has three distinct relationships: nested cards and green diamond lines
show composition or held objects; dashed purple arrows show calls and controls;
blue arrows transfer values. Cards name objects, values or explicitly labelled
responsibilities. Runner's Initialization, Execution and Maintenance cards are parts
of one implementation, not three invented public classes or independent processes.

## Read the system in order

1. **Author the scene.** Satellite orbital/capability inputs and GroundStation sites
   enter SEED construction separately. AS/Shell/Orbit/Satellite and GS objects remain
   on the Emulator side of an external adapter. The adapter copies receiver-owned
   values into SimulatorStaticInput; Definition adds the finite Timeline.
2. **Calculate.** Simulator owns PhysicalWorld, PhysicalLinkModel and Planner.
   Position/query results feed selection. Planner retains current state and publishes
   Plans containing timestamped Transitions and framework-derived event dependencies.
   Producer advances time and calls Compiler with the predecessor result.
3. **Publish.** Compiler lowering becomes sealed execution deliveries. A complete
   request publishes bounded readable history prefixes. Controller integrates these
   owners, controls and optional presentation without making rendering a calculation gate.
4. **Deploy and execute.** Deployer builds/starts the SEED Compose project. Runner
   acquires privileged Linux resources, activates prepared states and reclaims retired
   resources. These lifetimes are separate from calculation. Active Traffic probes
   the realized network; it does not own Runner's clock or forwarding state.
5. **Inspect.** Tools read calculated or observed records and write relative browser
   bundles. A page is a derived view and may lag a completed calculation. It cannot
   supply a second authority for formal coverage.

Construction alone starts no network. `serve()` starts owned calculation/control work;
`deploy.sh` starts deployment; `run.sh` acquires Runner/Traffic resources. Shutdown
waits for worker-owned completion signals and actual resource drainage.

- [Calculation and handoffs](calculation.md): physical queries, current state and Plans.
- [Models and framework flow](models-and-state.md): replaceable choices and fixed semantics.
- [Execution and controls](execution.md): delivery preparation, waiting and cancellation.
- [Mechanisms and tradeoffs](mechanisms.md): incremental state, bounded preparation and retention.
- [Terminology and source map](terminology.md), [developer references](../development/README.md).

Regenerate the vector with `python docs/architecture/system_architecture.py`.
The SVG is a derivative of that single editable source; local Mermaid diagrams explain
specific relationships at a smaller scale.
