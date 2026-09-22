# Traffic observation

This read-only Tool compares actual sender records and available packet/path
telemetry with planned facts. It never starts traffic. Workload selection,
scheduling and sending belong to [Traffic generation](../../generation/traffic/README.md).

With BPF telemetry, select an episode/transaction, seek or play the existing path
replay. Shell filters narrow scope. Planned paths, observed hops and missing
observations have distinct legend entries; a missing observation is not proof
that a packet was absent, and planned reachability is not proof of delivery.

Without packet telemetry, the page shows actual sender counts and execution-record
replay, with packet/path evidence explicitly unavailable. Counts cover completed
sender segments; raw replies remain inspectable for an incomplete run. It does
not turn missing BPF data into zero traffic or failed delivery. Linux generation
uses this same observation path when the scene provides GS/Zone routing. Native
transit remains unknown: actual replies and loss are reported without classifying
all loss as protocol convergence or inventing a planned path. Raw daemon/kernel
checks are needed to investigate loss causes. Per-Shell path coverage remains unproven.

Run a maintained Example and open `output/index.html`. The generation preview is
available before deployment; actual observations appear after runtime evidence
has drained. `python -m tools.observation.traffic --help` lists detached input paths.
Reopening or replaying the page sends no packets and reruns no Models.

[Historical Traffic demo](../../../docs/demos/s5/traffic/index.html),
[provenance](../../../docs/demos/README.md), [output guide](../../../docs/user-guides/outputs-and-tools.md).

## Native sender globe

Without BPF telemetry, observation retains a shared interactive globe: rotate/zoom,
select a communication, seek/play recorded runtime elapsed seconds, and inspect
sender changes and replies. One pass through sealed Plans produces compact per-frame
position, dynamic-connection and Zone-service context. The latest completed Runner
ACTIVE boundary selects the frame; recorded holds preserve it and source migrations
follow actual sender segments. Without runtime timing, only explicitly labeled
planned frame browsing is available. Missing spatial input retains sender-only output.

Purple dashed endpoint relationships are not observed paths. Native transit remains
unknown. Actual reply observations highlight their recorded sender, including the
correct segment after migration; aggregate segment totals never generate events.
The native page opens with the evidence boundary and links to globe/timed evidence,
sender totals/flows and raw execution records. Its legend explicitly identifies the
endpoint line as a communication relationship with no recorded intermediate packet hops.
Internet Map live observations are not automatically imported into Traffic replay.
The raw records and provenance hashes remain downloadable in `traffic-replay.json`.
BPF continues using its existing ingress-supported path replay. These views are
offline readers; no Model or traffic generator runs when opening/replaying them.
