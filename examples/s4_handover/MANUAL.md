# Manual network experiments

[中文](MANUAL.zh-CN.md) · [Build and run](README.md)

Complete [installation](../../README.md#installation), then build Scene, Emu and
plans using the main guide. Subsequent runs reuse them. Commands below run in Bash
from the root of your cloned repository.

```bash
conda activate skyseed_dev
source tools/deployment/shell.sh
python -m examples.s4_handover.run
```

For persistent shortcuts, follow [Tools setup](../../tools/README.md#shell-shortcuts).
The script ships with the repository and requires no author-specific workstation setup.

## Time and endpoints

In another terminal, enter the repository root and load the same environment and commands:

```bash
conda activate skyseed_dev
source tools/deployment/shell.sh
skytime       # Phase, total time, cycle/time within cycle, outage, serving node
skynodes      # Container names and IPs: UT, primary GS, two backups, serving node
dockps        # Running container IDs and names
docksh NAME   # Bash inside a selected container; exit leaves the container
```

The run terminal updates every second. Each cycle lasts 600 seconds; the configured
outage is at 180–240 seconds. In `run.py`, set `REPEAT = False` for one pass or
`CAPTURE = True` to retain endpoint PCAP. Manual mode leaves continuous capture off.

| Object | Select with | Purpose |
|---|---|---|
| UT | UT row | Stable `192.0.2.2`; client or listener |
| Primary GS | GS 1, currently Yamaguchi | Automatic TCP echo uses port 2323 |
| Backup GS | GS 2 Akita, GS 3 Hitachinaka | Same manual tests; no automatic migration of existing TCP sessions |
| Serving satellite | service row | Inspect UT attachment, egress and backbone interfaces |

The three GS are ordered by spherical distance from the Zone centre, independently
of route availability. Use their actual Compiler-assigned IPs from `skynodes`.
Unassociated GS do not receive this example's UT classifier.

## Bidirectional nc

Substitute actual container names and GS addresses. Open a terminal in each endpoint.

| Location | Command | Observation |
|---|---|---|
| Any associated GS container | `nc -l -k -p 2324` | Listen; 2324 avoids the automatic echo server |
| UT container | `nc -v GS_IP 2324` | Type a line and Enter; receive it at the GS, and vice versa |
| UT container, reverse initiation | `nc -l -k -p 2324` | Listen at the UT |
| Any associated GS container | `nc -v 192.0.2.2 2324` | Connect to the UT |

`-k` keeps the listener available for later connections. A client that exits does
not reconnect automatically: run `nc` again. An established TCP socket can wait
for retransmission during and after an outage; restoration does not reset its
backoff timer. Inspect `ss -tn` to distinguish waiting from a closed connection.
The automatic echo is a separate application; replies go to `tcp-client.jsonl`.

## Handover times and interface changes

Each `simu` build exports `output/observations/handover-times.md` under this example
from the actual planning observations. Generate this file locally; it is not part
of the source checkout. These are planned times in each cycle;
`skytime`, `attachment.jsonl` and replay show actual adoption/attachment. Initial
access may also wait for a usable path.

| When | Inspect | Expected change |
|---|---|---|
| Initial attachment | UT: `ip -br addr`, `ip route` | `ssutidentity` holds `192.0.2.2/32`; `ssut` and routes to three GS appear |
| Handover | UT: `ip -o link show ssut` | Name stays `ssut`; recreated interface and peer indices change |
| Handover | Old/new satellite: `ip -br link`, `ip route show 192.0.2.2/32` | Old node loses `ssut` and drops UT traffic; new node gains access and delivery route |
| Handover | UT/GS: `ss -tn` | Containers and application IPs persist; a pause alone is not a new TCP connection |
| 180–240 s | Affected GS: `ip -br link` | Ground links disappear; restored names can match but indices normally differ |
| Cycle boundary | Cycle number, interfaces, replay | Update only changed links/rules; a brief pause can accompany the artificial jump |

Access addresses remain satellite `169.254.254.1/30` and UT `169.254.254.2/30`.
Handover recreates veth while keeping application identity. Use `ip -br link` for
actual Compiler-assigned backbone interface names. BPF label tables are not the
Linux IP/MPLS FIB; use execution replay for label actions.

```bash
# Inside a selected container
ip -br addr
ip route
ss -ltn             # Listening ports
ss -tn              # Established/pending TCP sessions
# UT: original IPv4 TCP; Ctrl+C ends capture
tcpdump -ni ssut 'tcp port 2324'
# GS: choose a dynamic interface from ip -br link
tcpdump -nei INTERFACE 'ether proto 0x8847'
```

## Stop and outputs

Press Ctrl+C once in the run terminal and wait for cleanup and result generation.
Completed records are retained. The owned UT, measurement processes, Runner,
Compose deployment and etcd service stop; other experiments are untouched.
Frozen inputs live in `output/runs/<run>/inputs/`, records beside them, and views
under `views/`. The execution clock continues forward; each repeated topology is
an artificial return to the saved start, not propagation beyond 600 seconds.

After an abnormal exit, first confirm that the original run has ended:

```bash
docker compose -f examples/s4_handover/output/seed/deployment/docker-compose.runtime.yml -p skyseed-s4 down
docker ps -a --filter label=skyseed.example=s4-handover
docker ps -a --filter label=skyseed.runner
# Identify containers belonging to this run, then stop them individually.
docker stop EXACT_CONTAINER_NAME
```

The UT container is not auto-removed: follow with `docker rm EXACT_UT_NAME`.
A separately started Internet Map can remain running; stop it with
`docker stop seedemu_internet_map`. Generic `dcbuild/dcup/dcdown` operate in the
chosen Compose directory. `dcup` starts containers only, not Runner. Do not use a
global Docker prune to clean this example.
