# Node files, software and startup commands

Attach application files and commands directly to the nodes while constructing the
SEED scene, before generation. Satellites inherit SEED `ExtensionNode`; ground
stations inherit `Router`. Both expose the same SEED file, software and startup
APIs as ordinary ground routers. No separate loader is needed.

Here, **node payload** means those files, packages and commands. The satellite
constructor's `payload={...}` and the satellite's `.payload` property are separate
string metadata; they do not install files or execute programs.

## Select the actual nodes

Given the `emulator` from [Tiny's `build_emulator()`](../../examples/Sky00_tiny_network/tiny_network.py):

```python
base = emulator.getLayer("Base")
skyseed_as = base.getAutonomousSystem(65001)
shell = skyseed_as.get_shell("shell-1")
orbit = shell.get_orbit("orbit-1")

as_satellites = skyseed_as.satellites()       # All satellites in this AS
shell_satellites = tuple(shell.iter_satellites())
orbit_satellites = orbit.satellites          # Tuple property, not a method
satellite = skyseed_as.get_satellite("satellite-0")
same_satellite = orbit.get_satellite("satellite-0")
stations = skyseed_as.ground_stations()
station = skyseed_as.get_ground_station("gs-new-york")
ground_router = base.getAutonomousSystem(65002).getRouter("ground-router")

# Ordinary Python filtering works on the same node objects.
selected = [sat for sat in as_satellites if sat.orbit_index % 2 == 0]
gateways = [gs for gs in stations if gs.station_type == "gateway"]
```

These queries return the owned nodes, not copies or Simulator records. Choose one
selection and apply declarations once per node: AS, Shell and Orbit selections can
overlap. Mutating a selected node changes the scene that SEED will compile.

## Declare files and commands

Tiny already applies this block inside `build_emulator()`, after creating its four
nodes; do not append it again to an already configured Tiny scene:

```python
import shlex
from pathlib import Path

# In tiny_network.py, resolve relative to the source file, not the working directory.
hello_source = Path(__file__).resolve().with_name("hello.sh")
for node in (*skyseed_as.satellites(), station, ground_router):
    node.importFile(str(hello_source), "/hello.sh")
    node.appendStartCommand(shlex.join(["/bin/sh", "/hello.sh", node.getName()]))
```

`getName()` supplies the logical name, such as `gs-new-york`, independently of the
display name or Docker container name. `shlex.join` quotes arguments for the shell;
the script uses `printf 'Hello World from %s\n' "$1"`. Invoking `/bin/sh` explicitly
needs no executable permission on the imported script and no extra package.

The existing SEED calls cover other application needs too:

| Call | Effect with the SEED Docker compiler |
|---|---|
| `node.importFile(absolute_host_path, "/path/in/container")` | Copy an existing host file into the generated build context, then into the image. This is not a live bind mount. |
| `node.setFile("/etc/my-app.conf", "mode=demo\n")` | Declare text content at a container path, replacing a previous text declaration at that path. |
| `node.addSoftware("curl")` | Declare a container package requirement; SEED resolves it against the base image and adds package installation to the image build as needed. |
| `node.appendStartCommand(command)` | Append a shell command to container startup, in the foreground by default (`fork=False`). |
| `node.appendStartCommand(command, fork=True)` | Launch in the background so a long-running process does not block later startup commands. |

A foreground command must finish before subsequent startup commands run. Tiny's
one-line script exits immediately. Backgrounding does not wait for application
readiness or supervise restarts. Keep stdout/stderr attached to see output in
container logs. Quote variable arguments with `shlex.join` (or `shlex.quote` for
individual arguments).

These are build/deployment declarations, not immediate host execution or runtime
installation. Generation stages the files, Dockerfiles and startup commands;
deployment builds the images and starts containers. After changing declarations or
source files, regenerate and redeploy. Include authored files in the experiment's
`input_files` capture, as Tiny does for `hello.sh` alongside `zones.yaml`.

## Observe Tiny

Tiny enables the script by default on `satellite-0`, `satellite-1`, `gs-new-york`
and `ground-router`. Generation alone prints no greetings. Deployment produces four
`Hello World from <logical node name>` lines in container logs, one per node per
container startup; a container restart prints its line again. Runner does not trigger
these messages, and starting or resetting Runner on the same containers does not
repeat them. This leaves Tiny's topology, precomputed routing and simulation settings
intact.

Use the [Tiny deployment and log commands](../../examples/Sky00_tiny_network/README.md#node-startup-payload)
for a finite generation, deployment-only check and normal cleanup. The
[control guide](computation-and-controls.md) covers retained computation and simulation execution.
