# Runtime map

[中文](README.zh-CN.md) · [S4 manual](../../examples/s4_handover/MANUAL.md)

Open [InternetMap2](http://localhost:8080/pro/map) during the experiment. Click a node for interface details or double-click for a terminal. Filter and Log use SEED's packet capture. Select a GS or satellite to watch its Zones. Their service edges follow adopted local delivery rules.

```python
from skyseed import Runner
from tools.internet_map import InternetMap, ensure_image

ensure_image()
with deployment, Runner(iter(plans), deployment, output=run_dir, store=store) as runner:
    with InternetMap(scene, deployment, plans, run_dir, store=store):
        runner.run()
```

Supply a finite, re-iterable collection of physical plans in Runner order. Enter the map after deployment and Runner preparation. The context owns its container. The default port is 8080. Pass the same Store to watch the run's etcd event namespace, or omit it to follow the Runner's local execution journal incrementally.

| Display | Source |
|---|---|
| Fixed satellite rings and GS groups | Scene membership |
| Container identities | A startup inventory joined by Scene ID, including deployed nodes absent from plans |
| Links, interface names, addresses and directional properties | Successful link receipts joined to compiled targets and instructions |
| Node details | Docker state and interfaces of the selected container, refreshed when its links appear or disappear |
| Zone service | Optional GS associations and each satellite's adopted local delivery rules |
| Satellite coordinates | Optional saved ECEF samples at or before the displayed cycle time |
| Packet log | Container tcpdump sessions enabled by Filter, with manual JSONL download |

At planning time, `zone_associations(scene, routing_model)` returns associations for the default GZ model and its subclasses and an empty mapping for other models. Save these and pass them as `zones` when starting the map. UT, zones and `positions` are optional. S4 supplies a separate [UT bridge](../../examples/s4_handover/experiment/ut/map.py). A repeating experiment passes the initial cycle, one cycle including the wraparound operations, and the period in seconds.

## Source and image

- `layout.py` owns the fixed regions. `runtime.py` joins deployment and compiler data and manages the map container.
- `app/backend/receipts.js` follows receipts. `state.js` maintains the topology and revision history. `skyseed.js` serves state and on-demand node details.
- `app/frontend/src/skyseed.mjs` owns graph interaction and watched Zones. `capture.mjs` maps packet observations to interfaces and links.
- The adapted [SEED InternetMap2 source](https://github.com/seed-labs/seed-emulator/tree/80433bf813c8f9be23d4d9c7c9f7c914b5132da9/tools/InternetMap2) and [upstream license](app/LICENSE.txt) are in `app/`. `build_image()` builds the local `skyseed-internet-map` image from this directory. After editing the app, run `python map.py` in S4. Different scenes and plans share the image.

The service uses Linux host networking, binds HTTP to loopback, and mounts the run's inputs read-only. The Docker socket provides terminals, capture and on-demand inspection. Browsers request changes since their previous revision and receive a snapshot on entry or after a long interruption. Reopen the node details to inspect manual interface changes.
