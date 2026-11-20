# Manual network experiment

[中文](MANUAL.zh-CN.md) · [Build and run](README.md)

Complete the [build](README.md) first. With the local shortcut configured:

```bash
skyseed
cd examples/s4_handover
python run.py
```

The network repeats the saved 600-second scenario until Ctrl+C.
The following walkthrough uses the default S4 configuration.

## Enter nodes

In another host terminal:

```bash
skyseed
skytime
skynodes
docksh NAME
```

`skytime` shows cycle time and the current UT serving satellite. `skynodes` lists
UT, associated GS addresses and container names, and the serving satellite.
Use a listed container name with `docksh`; `exit` returns to the host.
`dockps` lists running containers.

## Send traffic

Start with Akita to observe inter-satellite forwarding. In UT, replace `GS_IP`
with the address from `skynodes`:

```bash
ping GS_IP
```

For TCP, run in the selected GS:

```bash
nc -l -p 9090
```

Then run in UT:

```bash
nc GS_IP 9090
```

Type a line and press Enter at either end; observe reception at the other end.
Keep these terminals open and use another terminal to inspect satellites.

## Observe interfaces and handover

In a node container:

```bash
ip -br addr
tcpdump -i any -nn
```

Capture shows that container's packets, including labels on MPLS packets.
Ctrl+C stops capture. At handover, `ssut` disappears on the old serving satellite
and appears on the new one. UT retains `192.0.2.2`; use `skytime` and `skynodes`
to locate its current service. `output/observations/handover-times.md` lists the
planned changes for comparison with the actual attachment.

To observe the regional outage, choose Yamaguchi and start a new ping or nc
session. Its configured interruption occurs at 180–240 seconds each cycle.
Continue sending to observe delivery before and after the interruption.

## Stop

Press Ctrl+C once in the running terminal and wait for UT and network cleanup.
`output/runs/<run>/` retains execution and attachment records and diagnostic logs.

After an abnormal exit, confirm that the original run has ended, then use the repository root:

```bash
docker compose -f examples/s4_handover/output/seed/deployment/docker-compose.runtime.yml -p skyseed-s4 down
docker ps -a --filter label=skyseed.example=s4-handover
docker ps -a --filter label=skyseed.runner
```

Identify remaining containers from this run and clean them up with
`docker stop NAME` and `docker rm NAME`.
