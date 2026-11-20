# S4 InternetMap2 adaptation

[中文](README.zh-CN.md) · [Manual](../../MANUAL.md)

`python map.py` copies the installed SEED checkout's `tools/InternetMap2` into
`output/internet-map2`, adds this adapter and builds `skyseed-s4-internet-map`.
`run.py` starts the map alongside the network and stops it on exit.
The SEED checkout remains the upstream source; generated copies live under output.
The build uses InternetMap2’s original Node 22 / pnpm / Vite setup. The page is
[InternetMap2](http://localhost:8080/pro/map).

The original graph, information panel, terminal windows, Filter and Log are reused.
The adapter generates fixed regions from Scene and updates nodes and edges in place every two
seconds. Double-click opens the existing terminal; selecting a link shows both ends.
Satellite numbers sit inside their circles, with the orbit group at the ring center;
search and traffic highlighting preserve node size.
Use `not ip6` in Filter to exclude IPv6 background traffic; `all` captures everything.
The original Log retains packet text, including MPLS labels. Flashing maps each
captured line to its container and interface; clear Filter and Submit to stop capture.

Ordinary links are visible around the selected node, with a Show all links override.
UT access and watched Zone service relationships stay visible. Packet capture is
independent of selection: observed links appear with a 2.5-second highlight.
GS/satellite details offer a Zone selector. Only watched Zones are added to the graph,
in stable slots below the rings; browser local storage retains those slots across refreshes.
Each page load also includes the current UT Zone; users can unwatch it during that visit.
Zone details reuse SEED's panel and console actions, and provide IP-filled ping commands
to copy. Purple dashed service relationships are separate from physical packet links.

| Display | Source |
|---|---|
| Group, fixed site, container identity | Scene and deployed containers' Scene identity labels |
| Identity IP | Compiled node target, when present; all interface addresses come from the live inventory |
| Container state | Direct Docker inspect of this run’s known containers |
| Interfaces, IP, link endpoints | Read-only netlink dumps in the containers' network namespaces; reciprocal veth indices |
| UT service | Attachment state, checked against the live access interfaces |
| Zone IP, location, associated GS | Compiled endpoint destination, Scene, saved build associations |
| Serving Zones | Final delivery adoption receipts, checked against the live satellite's ssidentity address |
| Link delay/loss | Adopted link instructions |
| Satellite position | Most recent saved ECEF sample at or before cycle time, with its timestamp |

All deployed Scene nodes appear, including GS nodes with no operations in the saved plans.

## File responsibilities and replacement

| File | Responsibility |
|---|---|
| `layout.py` | Generate satellite rings and UT/GS/Zone regions from Scene counts; display positions only |
| `temporary_ut.py` | Bridge this example's `terminal.json`, `attachment-state.json` and single-UT/`ssut` conventions |
| `runtime.py` | Collect Scene/deployment identities, compiled addresses and receipt indexes; own the map container |
| `collector.py` | Read live interfaces/veth peers and incremental adoption receipts; update Zone service and map state |
| `frontend.mjs` | Update SEED's graph, watched Zones, details and console actions |
| `capture.mjs` | Map SEED capture events to interfaces/edges; reuse its recording events and save JSONL |
| `backend.js` | Run-specific container inventory, console ID lookup, state API and observer process |
| `build.py` | Copy upstream, apply integration hooks, extract positions and build the image |

Ring radius follows satellites per orbit; the ring grid follows the orbit count.
GS occupy the right region, UT the left, and watched Zones sit below both satellites
and GS. Watched slots stay put across handovers and other Zone additions/removals;
nodes cannot be dragged. Adjust spacing in `layout.py` without copying a page for S5.

The temporary UT network implementation remains in the adjacent `ut/` directory.
A real UT system can replace it and `temporary_ut.py`, then adapt display data to
its own terminal count, locations and state. This single-UT file format is not a
required interface for that system. The map also depends on this example's BPF
adoption receipts, `ssidentity` and default GZ associations. AS150, port 8080,
project and image names remain example settings; changing shells does not require renaming them.

All visualization implementation lives here, with only the `map.py` build entry and
`run.py` import/context outside. A future visualization can remove those entries,
this directory and its tests/docs together, then clean generated `output/internet-map2`,
map state/position extracts and the image. UT, core and SEED code need no corresponding
rollback. Only a generated upstream copy is patched; the root README's recommended
SEED revision is sufficient. If its page structure changes, `build.py` identifies the
integration point that no longer matches.

## Manual traffic records

In Log, **Record → Stop recording → Save** reuses SEED's in-memory event list and
saves the current recording as JSONL. Each event keeps raw packet text (including
tcpdump time and MPLS labels), full node name, container, receipt time, and the
interface/peer/cycle time from the latest map snapshot. Filter selects what is
captured; recording generates no traffic. Save before refreshing, clearing Log or
starting a new recording: those actions clear the previous in-memory events.
Downloads go to the browser's download directory. These records support comparing
captures at different nodes; interface mappings carry their snapshot time, rather
than providing historical topology replay or automatic TCP measurements.

A shared two-second inventory inspects this run's containers with at most twelve
concurrent requests. Known IDs resolve directly for terminals, avoiding repeated
global Docker listings for terminals and capture.

The map container uses the Docker socket for SEED terminals/capture and the existing
host Python environment through a read-only host mount for namespace observation.
It binds to `127.0.0.1:8080`. Its label is `skyseed.example=s4-handover`.
`map-config.json` and the latest atomic `map-state.json` are kept in the run directory;
observer errors appear in `docker logs MAP_CONTAINER` and on the map.
The coordinate extract is about 4 MB for S4; the full offline observation file is
read only during extraction, stopping after its positions field.
