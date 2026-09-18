# ME2 example evidence — 2026-09-17

These are retained observations from before phase 4b-2. They do not validate the new
publication or control implementation. Original records live under
`skyseed-local/experiments/me2-20260917/current-results/`.

## ME2 generated observations (2026-09-17)

The full offline interval sealed **121 boundaries in four Plans/realizations** in
**49.789 s**; entry plus Tools took **63.868 s**. The actual policies produced:

- 3,547–3,554 feasible ISLs per frame and **343 operational ISLs**, unchanged over
  the formal interval after five-second prehistory preparation.
- 56–67 GS candidates per frame, **21–24 operational accesses**, and up to three
  concurrent preparations. There were 51 withdrawals and 52 new selections after
  time zero; no selected preparation failed in this observed interval.
- **68–71 of 184 Zones served**, **202–218 reachable** and **544–554 unreachable**
  route outcomes per boundary. Sparse geographic coverage is an observed result.

Recorded candidate measures/work statistics remain optional: no plane classification,
future window, underfill diagnostic or physical work count was fabricated. Capacity,
original selection times and real five-second acquisition were checked through
Planner and Compiler. Normal-page relationship interactions passed without a browser.
Generation used one effective route computation worker (limit eight, no process
pool needed). It overlapped S3/offline validation, so timings are references rather
than isolated performance comparisons.


## ME2 representative live result (2026-09-17)

One full run completed through **00:10:00**, with 121 activations (bootstrap plus
120 paced Main activations), no Runner failure or material wait, and ordinary
Traffic enabled. Deployment started 184 services in **21.341 s**; Runner preparation
took **4.502 s**, paced execution **600.007 s**, and Runner shutdown **1.116 s**.
The execution operation including detached Tools and deployment cleanup took
**661.761 s**. Deployment and Runner each used eight effective workers. Maximum
recorded Main time was 44.016 ms; maximum completion lag was 44.219 ms.

Traffic qualification and stable/transition coverage in both directions are
**PASS**: 19 episodes, 17 identities, at most six concurrent generators, **1,325
sent / 1,325 received / zero loss**, RTT median 37.8 ms and p95 48.5 ms, with no
telemetry ring loss or probe infrastructure failure. These bounded probes exercise
selected reachable paths; they do not imply universal coverage of the sparse scene.
Packet replay has 1,326 associated transactions, a distinct measure from the
executor's authoritative sent/received counters.

The first detached replay exposed an existing reader omission: compact Compiler
Zone prefixes were discarded, leaving packet-to-route associations unknown.
Passing through those already recorded facts corrected the ordinary Tool; replay
now classifies all 1,326 transactions as observed reachable. Original projection
and unchanged raw runtime evidence are preserved; no second network run was needed.

Runner confirmed removal of its remaining 367 link pairs, BPF pin root and retained
182 namespace descriptors/sockets, with zero owned/resident resource counts and
no cleanup errors. Project containers/networks and the privileged helper are gone;
the host interface inventory returned to its initial state. An independent root
`/proc`/bpffs scan was unavailable (`sudo -n` required a password), so descriptor
and pin removal verification uses the recorded privileged-helper acknowledgments.

Reference host: `lionheart-XPS-8960`, Ubuntu 24.04.4 LTS, Linux 7.0.0-31-generic,
Intel i9-14900K (32 logical CPUs), 60.5 GiB RAM, Docker 29.6.2. Validation used Conda
`skyseed_dev`, Python 3.12.13 at
`/home/lionheart/miniconda3/envs/skyseed_dev/bin/python`, pip 26.1.2. These are measured
conditions, not hardware requirements. No browser or visual acceptance was used.
