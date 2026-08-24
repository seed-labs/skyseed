# Backend architectural namespace

`skyseed_v2.backend` is the downstream namespace for approved network
realization backends. It is not a public facade, generic backend framework,
registry, lifecycle owner, or fallback selector; `backend/__init__.py` exports
nothing.

## Current responsibility

`backend/bpf/` is the primary/core optimized backend. It implements the
one-shot BPF Compiler, defines the immutable `BpfCompiledRealization` handoff,
and contains `BpfRunner`, the sole live BPF lifecycle owner. Its exact public
surface and implementation boundaries are documented in
[`bpf/README.md`](bpf/README.md).

The Simulator remains upstream semantic authority. A backend may consume only
its approved public facts/contracts and cannot own Simulator/Slicer/Planner
progression. Generated cross-domain application composition remains in the
private root module `skyseed_v2._generated_bpf_execution`, outside this
namespace.

## Future comparison scope

Native Linux / MPLS / IP comparison backends are an accepted future
architectural direction, but no concrete backend package, public API,
lifecycle contract, selection mechanism, or implementation has yet been
approved or implemented. When introduced through a separately approved
design, they remain secondary comparison/baseline backends; BPF remains the
primary/core optimized backend. No automatic selection, fallback, parity
requirement, or shared lifecycle ownership is established.

## Maintenance

Add another backend here only after explicit architecture approval of its
ownership, public contract, dependency direction, execution path, and human
example. File organization alone must not create a backend abstraction.
