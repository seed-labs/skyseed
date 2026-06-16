# Skyseed Node Role Boundary

This document defines the lightweight boundary between generic SEED-generated
infrastructure and Skyseed-specific node roles for Starlink-shell experiments.

## Purpose

Skyseed uses SEED Emulator for stable Internet infrastructure and container
generation, but Skyseed runtime nodes are not all generic dynamic-routing
routers.

The intent is to keep:

- SEED Mini Internet routers and hosts unchanged
- Skyseed runtime roles explicit
- startup behavior and readiness checks aligned with actual dataplane needs

The topology generation path now emits that boundary explicitly in:

- `skyseed_runtime_model.node_startup_profiles`
- `skyseed_node_role_framework`

## Current Roles

### Satellite Node

- role metadata: `satellite`
- startup profile metadata: `skyseed_satellite_mpls_only`
- current runtime semantics:
  - direct-backend dynamic links
  - timed runtime updates
  - MPLS / route programming through Skyseed runtime logic
- current startup reality:
  - still generated through the existing router-style SEED startup path
  - no broad daemon-stripping patch is applied yet

Why:
- the scale work proved that fragile satellite startup rewrites were riskier
  than the compose/runtime bottlenecks they tried to fix
- the metadata boundary exists now so a future dedicated satellite startup
  implementation can be introduced cleanly

### User Terminal

- role metadata: `user_terminal`
- startup profile metadata: `skyseed_ut_endpoint`
- placeholder carrier-router startup profile:
  `skyseed_ut_placeholder_carrier_only`

Current implementation:
- UT endpoints are treated as endpoints, not transit routers
- the UT placeholder carrier routers have router-daemon startup removed during
  generated output patching

### Skyseed Gateway / Ground Station

- role metadata: `gateway_ground_station`
- startup profile metadata: `seed_router_with_gateway_uplink_routing`

Current implementation:
- gateways remain border-routing nodes because they connect the Skyseed shell to
  the SEED Mini Internet
- they are distinct from ordinary SEED Mini Internet routers in topology
  metadata and runtime planning

### SEED Mini Internet Router Or Host

- startup profile metadata: `seed_mini_internet_default`
- remains unchanged by Skyseed-specific runtime patches

## Runtime Boundary

SEED keeps:

- container generation
- Mini Internet
- IXs
- stable gateway-to-Mini-Internet attachments
- static management/bootstrap networks

Skyseed owns:

- UT access links
- gateway GSLs
- same-plane ISLs
- cross-plane ISLs
- link-parameter mutation
- MPLS / route mutation

Dynamic satellite links must remain off Docker compose networks.

## Readiness Boundary

Real-runtime readiness should be based on:

- compose/container readiness
- scoped Skyseed preflight
- host MPLS readiness
- gateway reachability to validation targets

It should not assume that every Skyseed runtime node needs generic routing
daemon health as the primary readiness signal.

## Future Extension Points

The next clean architectural step is not a one-off patch per experiment. It is
an explicit Skyseed node-role generation API that can choose:

- startup script templates
- readiness probes
- daemon policy
- container capabilities
- runtime-agent bootstrap behavior

This document is the contract boundary for that future work.
