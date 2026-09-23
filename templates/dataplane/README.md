# Author packet processing and planned data

Copy [pass.bpf.c](pass.bpf.c), edit its handler, and select it in your existing
experiment assembly:

```python
from pathlib import Path
from skyseed.backend.bpf import BpfProgram
from skyseed.experiment import ExperimentController

controller = ExperimentController(
    emulator, simulator, output, entry_path=Path(__file__),
    dataplane_program=BpfProgram(Path("my_packet_program.c")),
)
```

Omit `dataplane_program` for the default. Old Example configurations need no edits.
The sample counts packets in a private mutable per-node map and returns PASS;
it intentionally leaves forwarding to the network stack, so it is not a replacement
routing algorithm for the Examples.

Include `skyseed.h`, implement `int handler(struct sky_context *)`, and finish with
`SKYSEED_PROGRAM(handler)`. Normal early returns still perform shared telemetry and
generation-reader release. Successful tail calls and other transfers that do not
return to the framework violate this completion contract. Authors own packet
bounds, policy and kernel-verifiable code. The framework supplies generation
protection, logical interface binding, observation and resource cleanup.
`sky_result` and `sky_redirect` optionally report reason/output; plain returns
leave that metadata unknown. PASS does not assert local delivery.

Local headers work normally. Add `include_dirs=(Path("include"),)` if needed;
`BpfProgram((Path("entry.c"), Path("helpers.c")))` compiles and links multiple units.
Use the framework header in the entry unit and ordinary local helper declarations
elsewhere. `BpfProgram(Path("prepared.o"))` accepts an already linked object with
the same C ABI/declarations, without compiling it. Materials include the object
and dependency provenance; Runner never needs author sources or modules.

C preparation needs Clang/LLVM BPF support, Linux UAPI and libbpf headers plus
`libbpf.so.1`; on Ubuntu install `clang llvm libbpf-dev libelf-dev linux-libc-dev`.
Execution needs libbpf and the existing runtime host capabilities, without Clang.
For planned data and multiple candidate exits, use the
[threshold walkthrough](threshold/README.md): define Python data, run the standalone
driver to generate headers, include the generated header, write C, run desired
checks, and prepare normally. The core `RouteData.generate_headers(directory)`
works even while the Model and C are unfinished. Additional maps remain mutable
object state across generations. Use `DataPlane`/`AttachmentScope` for explicit
node coverage, including empty coverage. Each covered node uses one object for
network ingress and fixed local-output ingress; `sky_is_local(ctx)` identifies
local origin. The same private mutable maps serve both. PASS returns to Linux
without recursive injection and does not create a forwarding route.

Read the [authoritative contract](../../docs/development/dataplane.md) for supported
representations, replacement semantics, scopes, compatibility and completion.
