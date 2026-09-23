# Threshold and multiple logical exits

This three-node teaching example chooses a local exit from packet length. It does
not claim a fault algorithm or establish end-to-end reachability.

1. Edit [definition.py](definition.py): `DATA` declares a rule threshold, per-candidate
   weight, and capacity two. Values are not filled yet.
2. From the project root, run `python templates/dataplane/threshold/develop.py headers
   --output /tmp/threshold-authoring` (on one line). This imports only the definition
   and writes `/tmp/threshold-authoring/generated/threshold.h`; C and Models can be unfinished.
3. Include `threshold.h` and edit [handler.bpf.c](handler.bpf.c). Generated structs,
   lookup/candidate access and logical-exit forwarding helpers replace handwritten
   layouts. The handler uses threshold; the recorded weight has no automatic effect.
4. Edit the imports, constructed Models, scene and paths in [develop.py](develop.py).
   Run `model`, `scene`, `continue`, `c`, or `materials` instead of `headers`. Choose
   a fresh output directory per stateful trial. Each operation runs separately;
   importing the driver does nothing. `model` checks call shape, `scene` validates
   Planner results, `continue` exercises a suffix, `c` compiles/inspects without
   loading, and `materials` runs normal lowering/encoding into sealed artifacts.
5. In an ordinary experiment, pass
   `BpfProgram(Path("handler.bpf.c"), data=DATA, authoring_directory=Path("generated"))`
   as `dataplane_program=` to `ExperimentController`. Leave it omitted in existing
   Examples to retain the default.

[scene.py](scene.py) visibly constructs fresh Model instances and a static triangle
of two satellites and a ground station. `ThresholdRoutes` fills threshold/weight
values and logical connection exits at each step. A offers G directly as its first
exit and B as its second; B offers only G. The C handler sends packets shorter
than 128 bytes along A→G and packets of at least 128 bytes along A→B→G, then
returns Linux PASS at G. B never selects a return hop to A. These choices belong
to this teaching policy; the framework does not detect loops in user algorithms.
That disposition alone is not proof of delivery. Its private `packets` counter
persists across managed-data publications. Locally originated business packets at
A execute this same C selection through the framework's fixed ingress; no extra
C entry or destination-specific Linux helper is needed. Local addresses at G
remain subject to normal Linux local delivery.

There is no population adapter, author binary encoder, Plan edit or runtime author
module. The [contract](../../../docs/development/dataplane.md) defines representation,
logical scopes, managed/mutable ownership and offline validation limits. The
[development helpers](../../../tools/development/README.md) can also check other
Model roles or stop before routing/C is ready.
