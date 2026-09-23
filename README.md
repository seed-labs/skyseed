# SkySEED

SkySEED builds satellite-network experiments on [SEED](https://seedsecuritylabs.org/emulator/).
An Emulator constructs the network scene; replaceable Models compute physical
conditions, connections, Zone service and routes. Computation publishes Plans and
sealed execution materials. A separate Deployer and Runner deploy the SEED containers
and apply network changes through Linux/BPF. Optional interactive views explain the
physical world, planned network and observed execution without becoming their owners.

The official repository is
[seed-labs/skyseed](https://github.com/seed-labs/skyseed), branch `main`; access is required.

## Start here

| I want to… | Start with |
|---|---|
| Understand the system | [Architecture and system diagram](docs/architecture/overview.md) |
| Explore without running an experiment | [Download/local interactive demos](docs/demos/README.md) |
| Run the default assembly example | [S4](examples/Sky02_s4_default/README.md) |
| Run full S4 GS↔Zone traffic with BIRD OSPF | [Native S4 workflow](examples/Sky06_s4_native_ospf/README.md) |
| Try a smaller scene and customize Models | [S5 and its local algorithms](examples/Sky01_s5_custom_models/README.md) |
| Choose Linux or BPF forwarding | [Shared pipeline and mode differences](docs/architecture/overview.md#choose-a-forwarding-mode) |
| Replace packet processing | [C/eBPF template](templates/dataplane/README.md) |
| Look up public methods and Model input data | [API reference](docs/api/README.md) |
| Read state, exchange user data or revise future work | [Feedback from host and container programs](docs/user-guides/computation-and-controls.md#interaction-setup-and-independent-programs) |
| Develop or review changes | [Developer references and workflow](docs/development/README.md) |
| Explore satellite-network research and collected evidence | [Research archive](research/README.md) |

## Install, calculate and run

Follow [getting started](docs/user-guides/getting-started.md) to obtain the
required SEED fork and install Conda/Python dependencies. A browser can open the
retained demos locally; they are not an already hosted website. Actual network
execution additionally needs the [Linux/Docker/BPF host](docs/user-guides/linux-host.md).

```bash
# Terminal 1, from this repository; SEED checkout prepared by the installation guide.
bash scripts/setup_dev_environment.sh
conda activate skyseed_dev
python scripts/check_dev_environment.py
python scripts/setup_interaction.py  # Install the user-local etcd binary; starts no service.
python examples/Sky02_s4_default/s4.py
```

The example constructs the scene, computes 0–600 seconds, publishes results and
optional views, then keeps the computation process waiting for continuation. Open
`examples/Sky02_s4_default/output/index.html` after publication. This does
not automatically deploy or run a network. Add `--operation generate` for finite
generation and exit. [Tiny](examples/Sky00_tiny_network/README.md) is the
installation smoke test.

```bash
# Terminal 2, from this repository, after results are published and host setup is complete.
conda activate skyseed_dev
cd examples/Sky02_s4_default/output
./deploy.sh
./run.sh
```

```bash
# Terminal 3, from this repository; run.sh retains the last ACTIVE network at range end.
conda activate skyseed_dev
cd examples/Sky02_s4_default/output
python experiment_control.py status
# After the intended ACTIVE coverage has completed:
python experiment_control.py shutdown
# Wait for Terminal 1's computation and Terminal 2's run.sh to return, then:
./cleanup.sh
# Retain the service for result queries, or explicitly stop it after owners close:
python -m skyseed.experiment.interaction_service stop .
```

Cancellation acceptance is distinct from completed drainage. The
[control guide](docs/user-guides/computation-and-controls.md) explains
continuation, pause/resume, reset and separate cancellation. Generated controls and
`output/index.html` are the normal entry points.

## Choose a starting scale

| Example | Satellites / GSs / Zones | Initial range | Reference first material / deployment |
|---|---:|---:|---:|
| S5: smaller customization example | 172 / 10 / 184 | 600 s | 16.4 s / 21.0 s |
| S4: default assembly example | 348 / 355 / 732 | 600 s | 62.2 s / 70.9 s |

These measurements used a shared 32-logical-CPU, approximately 60.5 GiB Ubuntu host;
they are not minimum requirements. S5 is the smaller starting point, with no verified
8 GiB support guarantee. [Scale and resources](docs/user-guides/scale-and-resources.md)
gives S3/five-shell comparisons, concurrency, memory caveats and Traffic WARN outcomes.

[Documentation](docs/README.md) · [Current capability and validation](docs/CURRENT.md).
The retired v1 implementation remains in Git history at `375501cfba5ee385486105f382324729914dc3b1`.
