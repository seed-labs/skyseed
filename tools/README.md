# Repository Tools

[中文](README.zh-CN.md)

- [Observation](observation/README.md): read saved Scene, logical plans, compiled
  instructions and actual execution records. These tools do not run the network.
- [S4 view.py](../examples/s4_handover/view.py): a short example of independent
  Scene/Physical/Link/Network/Compiler exports.
- `deployment/build_images.py`: build reusable images and a runtime Compose file
  from SEED deployment output, as shown in the S4 Emu entry.
- [Shell shortcuts](deployment/shell.sh): SEED-style Docker commands, environment
  entry and S4 runtime queries; setup below.

Tools are optional repository utilities, separate from the `skyseed` package.

## Shell shortcuts

After [installation](../README.md#installation), load the script in Bash from the
repository root. It locates the checkout from its own path:

```bash
source tools/deployment/shell.sh
skyseed
```

| Command | Purpose |
|---|---|
| `skyseed` | Activate the setup script's `skyseed_dev` Conda environment and enter this checkout |
| `dockps` | List running container IDs and names |
| `docksh NAME` | Open Bash in the selected container |
| `dcbuild`, `dcup`, `dcdown` | Docker Compose build, up and down in the current Compose directory |
| `skytime`, `skynodes` | Read S4's current phase, cycle, endpoints and serving satellite |

Load the script in each new terminal. To keep the commands available, add
`source /absolute/path/to/skyseed/tools/deployment/shell.sh` to `~/.bashrc` **after
Conda initialization**, replacing the path with your checkout. This only defines
commands; it does not activate an environment or start containers at shell startup.
With a differently named environment, activate it yourself instead of using `skyseed`.

The Docker shortcuts wrap the same commands used in SEED environments; they do
not require a separate SEED shell configuration. `skytime` and `skynodes` require
S4 to have started on this machine; once it stops they show its last recorded
state. See the [manual experiment guide](../examples/s4_handover/MANUAL.md) for usage.
