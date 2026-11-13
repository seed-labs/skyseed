# Using SkySEED

[中文](usage.zh-CN.md) · [Reference](reference.md)

Start with [installation](../README.md#installation). Run repository examples and
Tools from the repository root in the configured Python environment.
Optional [shell shortcuts](../tools/README.md#shell-shortcuts) provide `skyseed`,
`docksh`, Docker Compose commands and S4 runtime queries.

## Build independently

| Stage | What you write | Reusable result |
|---|---|---|
| Scene | Nodes, groups, orbital inputs, GS and endpoints | `Scene.dump(...)` / `Scene.load(...)` |
| Emulator | AS assignment, SEED networks, software and services | Unrendered SEED description; rendered deployment files |
| P&C | Models and planning intervals | Base/execution LinkPlans; base/Delivery RoutePlans |
| Compiler | Deployment directory and forwarding backend | PhysicalPlans with backend instructions |

Scene can be used without Emulator or P&C. Emulator does not need plans to build
nodes. Logical planning does not need a deployment; **compilation** reads deployment
files to assign stable targets. Runner then resolves this run's actual resources.

[The tiny network](../examples/tiny_network/README.md) demonstrates Scene and SEED
construction. [S4](../examples/s4_handover/README.md) has separate `scene.py`, `emu.py`,
`simu.py`, `view.py` and `run.py`. Its UT, outage and measurements are explicit example
extensions. Read that configuration before starting the full experiment.

## Generate and save plans

Given a saved Scene and matching SEED deployment files:

```python
from datetime import timedelta
from skyseed import Scene, PlanningControl, PlanCompiler, save_plans, load_plans

scene = Scene.load("output/scene/scene.scene")
pc = PlanningControl(scene)
start = scene.get_shells()[0].epoch
end = start + timedelta(seconds=60)

save_plans("output/plans/links.plan", pc.generate_link_plans(start=start, end=end))
save_plans("output/plans/routes.plan", pc.generate_route_plans(start=start, end=end))

compiler = PlanCompiler("output/seed/deployment", forwarding_backend="bpf")
save_plans("output/plans/physical.plan", compiler.compile_plans(
    link_plans=load_plans("output/plans/links.plan"),
    route_plans=load_plans("output/plans/routes.plan"),
))
```

`timedelta` is Python's duration type. Choose one timezone-aware start for the
experiment; reading a shell epoch is convenient when that is the desired start.
Scene does not require all input sources to share one epoch.

Requests calculate when their iterators are consumed. Link and route requests
share base-link computation but remain separate outputs. Continue forward from
the preceding requested endpoint. Delivery tails are included automatically;
they do not change where the next request starts. A separate experiment uses new
model instances. Saved plans contain values, not a checkpoint of live models.
See [P&C](../src/skyseed/pc/README.md) for continuation and file details.

The default route model generates MPLS-like labels. For ordinary IPv4/Linux,
configure `GroundStationZoneRoutingModel` with `label_forwarding=False` and use
`forwarding_backend="linux"` in the compiler. The compiler does not silently
convert label actions into IP rules.

## Replace models

Use the owning layer's setter before generation. A class only needs the documented
minimum methods; inheritance from the built-in implementation is optional. Its
constructor settings go through `model_options` to that model.

```python
from skyseed.pc.link import PeriodicLinkPlanModel

link = pc.get_layer("link-layer")
link.set_plan_model(PeriodicLinkPlanModel,
                    model_options={"interval": timedelta(seconds=10)})
```

The [model reference](reference.md#models) points to minimum interfaces and defaults.
Physical position/candidate models are required and replaceable. Perturbation is
optional: it edits the execution-link branch without changing the base links used
for routing. Delivery Scheduling edits route adoption times. The example's
[regional outage](../examples/s4_handover/experiment/perturbation.py) demonstrates a
concrete hook without introducing a general interference framework.

## View saved results

[Tools](../tools/observation/README.md) consume saved Scene/Plan files and optional
drawing observations. The calculation side saves positions and model observations
with `save_context`; the view side never reruns models.

```python
from tools.observation.plan_views import write_scene, write_compiler

write_scene("output/scene/scene.scene", "output/views/scene")
write_compiler("output/plans/physical.plan", "output/views/compiler")
```

Open the returned `index.html` directly. Link/Network/Physical views additionally
use the saved drawing context; see [S4 view.py](../examples/s4_handover/view.py).
Tools are repository utilities, not installed as part of the core wheel.

## Run and clean up

```python
from skyseed import Deployment, Runner, Store, load_plans

deployment = Deployment("output/seed/deployment")
store = Store("http://127.0.0.1:2379", namespace="skyseed/run-001")
with deployment:
    with Runner(load_plans("output/plans/physical.plan"), deployment,
                output="output/runs/run-001", store=store) as runner:
        runner.run()
```

Build deployment images first. Here etcd is already running; the application owns
its startup and shutdown. S4's `emu.py` builds images and `run.py` manages an
experiment-local etcd through its helper. `with` is Python's resource context:
Deployment starts/stops Compose; Runner prepares/releases its execution resources.
When finite input finishes naturally, `run()` returns after planned work and
delayed tails have drained. Calling `Runner.stop()` instead records `stopped`;
unexecuted work is not marked complete. Cleanup occurs on leaving the contexts.
See [Runner](../src/skyseed/runner/README.md#lifecycle-and-timing) for the lifecycle.
Plan time is not shifted when an operation is late.

Store provides shared state and revisioned subscriptions between processes.
Runner also writes `execution.jsonl`; a standalone run may omit Store when shared
access is unnecessary. Offline analysis reads the files, without etcd.
Use a fresh run directory; do not reuse live resource identities between runs.

When using the repository's `build_images` Tool, pass
`compose_file="docker-compose.runtime.yml"` to Deployment, as S4's `run.py` does.
The default `docker-compose.yml` refers to SEED's original deployment files.

## Runtime requirements

The live examples target a Linux host with local Docker Engine; Docker Desktop's
VM and a remote Docker daemon do not share the host namespaces used by Runner.
On Ubuntu 24.04, install [Docker Engine and Compose](https://docs.docker.com/engine/install/ubuntu/)
and follow its [non-root access setup](https://docs.docker.com/engine/install/linux-postinstall/).
Check access from the same terminal used for Python:

```bash
docker info
docker compose version
sudo apt update
sudo apt install clang linux-libc-dev libbpf1 iproute2 tcpdump
```

`clang` and the Linux headers compile the BPF program; `libbpf1` loads it.
`iproute2` supplies network tools, and the S4 UT helper uses the host's `tcpdump`
for packet capture. If these host libraries/tools are already installed but
clang is missing, `conda install -c conda-forge clang` in the active environment
provides the compiler without changing the host installation.
Before building the full example, check compilation without deploying anything:

```bash
python -c 'from skyseed.pc.compiler.bpf.compile_program import compile_label_program; print(len(compile_label_program()), "BPF ELF bytes")'
```

Initial image builds need network access to the SEED image registry and package
repositories. Later builds can reuse Docker's cache. S4 also downloads its etcd
image on first use; it does not require an existing etcd service.

- Linux with veth, namespaces and TC/netem; permission to run privileged Docker
  containers and Docker Compose.
- BPF mode additionally needs clang with the BPF target, Linux/BPF development
  headers and libbpf. Runner compiles the packaged program before timed execution.
- SEED nodes provide their normal tools/software. `all.rp_filter` must be disabled
  by the node setup for BPF local injection. See [Runner](../src/skyseed/runner/README.md).
- A running etcd v3 endpoint for Store. S4's helper starts the
  `quay.io/coreos/etcd:v3.6.5` container on a random localhost port and stops it
  after archiving records.

The included full example precomputes before running. The interfaces also accept
successive finite requests and plan batches; that is not a built-in multi-machine
scheduler. Visibility, route existence and continuous application service depend
on the scenario and models. A plan path, an applied-rule path and a captured packet
are different observations.
