# Current capabilities and validation limits

SkySEED uses one authored scene → physical computation → logical Plans → selected
Compiler translation → sealed materials → Runner pipeline. [BPF/Linux navigation](architecture/overview.md#choose-a-forwarding-mode)
locates each mode's assembly, planning, execution and observation responsibilities.
This page states the current scope; dated changes and experiments belong in [history](history/README.md).

## Implemented capability

- **Scene and payloads:** required ExtensionNode-capable SEED integration, ordinary node
  software/files/startup and [payload selection](user-guides/node-payloads.md). The optional
  native software adapter uses public Node facilities, validates before mutation and keeps
  BIRD/FRR policy in author code. It does not require extra SEED Routing convenience methods.
- **Planning:** seven replaceable Model roles; full paths and local source/target arrangements
  with logical candidates and immutable policy values. Empty/partial arrangements are valid.
  Default Simulator assembly selects BPF gateway routes or empty Linux routes; explicit Models
  are unchanged. Definition/Planner values have no backend execution dependency.
- **BPF:** default or authored C/ELF packet programs, declared structured routing data,
  node-level selection, fixed local-output ingress, prepared tables, atomic forwarding
  publication and reader-safe reclamation. The public `DataPlane` type is BPF-specific;
  it is not a universal backend plugin interface.
- **Linux:** native IPv4 identities, /31 dynamic links, directional parameters, moving Zone
  service addresses and supported explicit full-path/local FORWARD, PASS or DROP outcomes.
  Real routing daemons are node software. Runner executes ordered prepared kernel operations;
  it supplies neither routing policy, missing routes nor atomic rollback.
- **Lifecycle:** retained calculation by default, finite generation, chronological continuation,
  independent deployment/runtime owners, captured user preparation after B0 and before formal
  time, pause/resume/reset/cancellation and joined cleanup. Console feedback distinguishes
  acceptance, drainage, resource cleanup and later Tool generation; control stdout stays JSON.
- **Tools:** development checks, Traffic generation/preview and read-only observation remain
  separate. BPF supports recorded ingress/hop replay. Native Traffic shows sender replies,
  migrations and planned topology/service context, with no recorded intermediate packet hops.
  Dynamic Internet Map/terminal/capture support uses separately reviewed **local SEED Map
  changes**, not guaranteed by the ExtensionNode commit or unmodified upstream.

Current formats: planning evidence v7, semantic state v4, Plan/transition v6; execution
materials v8; outer execution definition v6 with existing v5 no-hook compatibility;
BPF foundation `skyseed-tc-elf/3`, Linux foundation `skyseed-linux-ipv4/2`.
[Compiler/Runner](development/compiler-and-runner.md) and [BPF authoring](development/dataplane.md)
own compatibility details. Regenerate incompatible materials rather than relabeling them.

## Evidence and its limits

- [Programmable-runtime capability evidence](history/programmable-runtime-expansion.md)
  records focused C/ELF, Models, native operation, software/preparation, continuation,
  cancellation, Docker and browser checks under their actual source conditions.
- The [earlier six-example BPF campaign](history/2026-09-18-closing-campaign.md) covered
  Tiny, S5, S4, precomputed S3, continuation and five shells. Its successful coverage and
  remaining Traffic losses apply to the recorded implementation; it does not qualify
  every later custom packet handler or native configuration.
- [Historical native S3 experiments](history/native-s3-experiments.md) completed two
  600-second edge-only runs with incomplete end-to-end convergence. A later GS↔Zone FRR
  run stopped at 250 seconds amid severe host pressure. Its cause remains unresolved.
- [S4 experiments and acceptance](history/native-s4-experiments.md) distinguish the
  fixed-pair FRR/BIRD runs, later sampled-GS/Zone runs and the accepted user invocation
  `20260922T061416.080496065Z-2047246`. The user completed 600 seconds, cancellation and
  cleanup and reported an inter-satellite Map ping. That output is preserved. Later
  presentation/adapter corrections have focused checks, not another full S4 campaign.

Complete runtime coverage is not universal reachability, continuous all-pairs OSPF
convergence or lossless forwarding. Retained daemon/kernel warnings and native losses
are not all diagnosed. Map observations do not automatically become Traffic replay
telemetry. Small/offline tests, live execution and user visual acceptance are distinct.
See [host requirements](user-guides/linux-host.md) and [measured scale](user-guides/scale-and-resources.md);
shared-host measurements are not minimum hardware guarantees.

## Remaining work

Runtime feedback and revision of future decisions belong to a subsequent conversation.
Mixed BPF/Linux forwarding, IPv6/SRv6, NAT, arbitrary external material import, public
backend plugins, process-restart recovery, mid-session stage promotion and automatic
rollback are outside current scope. See the [roadmap](plans/roadmap.md). Research notes
retain their dated hypotheses; they are not implementation promises.

The reviewed usability behavior is retained. Local SEED changes remain a separate
dependency review and are not part of a SkySEED-only phase commit.
