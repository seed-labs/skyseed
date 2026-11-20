# Temporary UT attachment

[中文](README.zh-CN.md) · [Experiment](../README.md)

This example adapter creates one UT container with a stable application address
and connects it to the satellite currently serving its Zone. It follows actual
route-adoption receipts, including handovers and repeated plan intervals.
Applications and packet capture are started manually by the user.

```mermaid
flowchart LR
    UT["UT · 192.0.2.2"] -->|changing access veth| Sat[Current serving satellite]
    Sat -->|MPLS over original IP| Net[Satellite network]
    Net --> GS[Associated GS]
```

| File/object | Responsibility |
|---|---|
| `routing.py:TerminalRoutingModel` | Classify the UT /32 at each associated GS onto its existing Zone label path |
| `attachment.py:TerminalAttachment` | Own the UT container and one attachment helper; wait for cleanup |
| `attachment.py:Attachment` | Maintain the access veth and temporary routes, following adopted service rules |

```python
network.set_routing_hook(TerminalRoutingModel, model_options={"ground_stations_per_zone": 3})
# Save and compile plans, then in the run stage:
with TerminalAttachment(scene, deployment, plans, run_output,
                        ground_stations=build["experiment"]["ground_stations"]):
    runner.run()
```

For repeated plans, pass the same `repetition` object used by Runner.
The helper opens network namespaces only for the UT and its possible serving
satellites. It does not start programs inside GS containers.

## Addresses and forwarding

| Address | Purpose |
|---|---|
| UT `192.0.2.2/32` on `ssutidentity` | Stable application identity across handovers |
| GS addresses from `skynodes` | Compiler-assigned node identities for manual communication |
| Satellite `169.254.254.1/30`, UT `169.254.254.2/30` | Next hops on the temporary `ssut` veth |

UT packets keep their original IP addresses. The serving satellite pushes a
label; intermediate satellites swap labels; the destination GS pops and delivers.
Replies use the UT /32 classifier at the GS, follow the Zone label path, and
leave the serving satellite through its temporary route to the UT.
There is no IP-in-IP or address translation.

When service changes, the adapter removes the old access veth and creates the
new one. The UT container and application address remain, so applications can
keep their sockets open. Without a serving node, access routes drop traffic.
A route receipt selects the service; planned times alone do not trigger attachment.
If no satellite serves the UT anywhere in the saved plans, startup reports this
and keeps the UT unattached while the network continues running.
`terminal.json` lists associated GS in `ground_ips` only when the plans provide
their node identity addresses. `build.json` retains all associated GS.

This is a temporary adapter for this example, not a general UT mobility protocol.
It adds no separate radio link budget, delay or loss model.

## Runtime state and cleanup

`terminal.json` identifies the UT, associated GS, candidate serving containers
and helper configuration. `attachment-state.json` holds the current serving
node. `attachment.jsonl` and `terminal-helper.log` describe attachment changes
and failures; Runner's `execution.jsonl` supplies the adopted-rule events.

The adapter owns its container, helper, interfaces and temporary routes. Leaving
the context releases them, including partial setup after a failure.
It does not launch echo servers, clients or tcpdump, archive measurements, or
generate result pages. Use the [manual](../../MANUAL.md) to run your own commands.
