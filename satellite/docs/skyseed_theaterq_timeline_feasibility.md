# Skyseed TheaterQ Timeline Feasibility

Goal 13 checks whether the Skyseed ParameterEpoch qdisc timeline can move
toward a TheaterQ-style trace-driven qdisc path. This is not a canonical
runtime landing, not a long real-runtime run, and not a tc-netem optimization.

Artifacts:

- `satellite/apps/probe_theaterq_timeline.py`
- `satellite/baselines/skyseed_theaterq_timeline_summary.json`
- `satellite/test_theaterq_timeline_probe.py`

## TheaterQ Trial

TheaterQ was cloned only into a temporary directory:

```text
/tmp/skyseed-theaterq-ixLT0v/TheaterQ
```

Source was not vendored into this repository. The trial used:

```text
repository=https://github.com/cs7org/TheaterQ
commit=0e7da6d802796a0b992858f5d7844d4b2d4173fc
branch=main
license=GPL-2.0
iproute2_submodule=72f679c0d07629fe9e462c2c52bbe48aaeaa7f83
```

Observed build requirements:

- `git`
- `make`
- compiler / build-essential
- linux headers for the running kernel
- `iproute2`
- `bison` and `flex` for the iproute2 tc plugin build

No apt packages were installed during this goal. If continuing the tc plugin
build on this host, the missing Ubuntu apt packages are:

```text
bison flex
```

The kernel module build did not succeed on this host:

```text
kernel=5.4.0-54-generic
lkm_build_success=false
```

The blocker is kernel/source compatibility rather than a missing ordinary
runtime artifact. The build fails with C90/Werror declaration errors and with
missing or incompatible helpers such as `skb_list_walk_safe` and
`tc_qdisc_stats_dump`. This is the point where changing kernel version or
porting an out-of-tree module would be required, so the trial stopped before
`insmod`.

The tc plugin build was attempted after submodule initialization. It did not
complete because the iproute2 build needs `bison` and `flex`. Since the kernel
module was already blocked, dependencies were not installed just to finish a
plugin that could not create a working qdisc on this host.

No TheaterQ module was loaded. No TheaterQ qdisc was created.

## Trace Mapping

The Skyseed input is the existing Goal 12 ParameterEpoch artifact:

```text
satellite/output/starlink_shell_experiments/gz-z6metadata-2h-gated-20260611-0824
```

Activation time comes from the event-time artifact:

```text
fixed_10s_assumed=false
activation_relative_time_seconds / activation_time_utc
```

TheaterQ simple CSV mapping:

```text
KEEP,LATENCY,RATE,LOSS,LIMIT
```

Skyseed fields:

- `delay_ms` maps to TheaterQ `LATENCY` in nanoseconds.
- `bandwidth_mbps` maps to TheaterQ `RATE` in bits per second.
- `loss_percent` maps to TheaterQ `LOSS` as a scaled u32.
- `routing_cost` remains Skyseed metadata and is not a qdisc field.

Coverage from the artifact:

```text
delay=3243/3243
rate=3243/3243
loss=3243/3243
```

Queue and reorder are absent in the current ParameterEpoch artifact. The trace
generator can fill TheaterQ syntax defaults such as `LIMIT=1000` and
`ROUTE_ID=0`, but those are explicitly recorded as not Skyseed-derived.

Example simple trace rows:

```text
30000000,2793620,559915333,4361883,1000
50000000,2602489,596809333,3996209,1000
30000000,2732184,571000000,4252018,1000
```

## Isolated Demo

Because TheaterQ did not build/load, no TheaterQ trace-driven qdisc demo was
run. A small netns/veth tc-netem replay scaffold was run only to validate the
shape of an isolated replay harness:

```text
initial=qdisc netem ... delay 2.0ms loss 0.01% rate 500Mbit
changed=qdisc netem ... delay 8.0ms loss 0.2% rate 100Mbit
```

This userspace replay scaffold is only a format and harness validation step. It
does not preload a trace into a qdisc, does not prove TheaterQ timing behavior,
and is not an optimized ParameterEpoch backend.

## Cleanup

After the isolated scaffold:

- test netns were deleted;
- test veth links were deleted;
- no `sch_theaterq` module was loaded;
- no TheaterQ qdisc existed;
- TheaterQ source remained outside the repository.

The temporary clone can be recreated from the recorded commit if needed; it is
not a repository artifact.

## Result

The Skyseed ParameterEpoch timeline can be converted into TheaterQ trace shape
for delay, rate, and loss. That is a real semantic fit for a trace-driven qdisc
route.

TheaterQ itself is not available on this host yet because the out-of-tree
kernel module does not build against the running kernel and the tc plugin build
also needs extra parser-generator packages. This should not land in canonical
runtime until kernel compatibility, plugin packaging, isolated load/unload, and
repeatable cleanup are solved.

Risk items:

- out-of-tree kernel module compatibility;
- running-kernel/header version dependence;
- tc plugin build and deployment path;
- trace ingestion via character device;
- cleanup of qdisc/module/netns residue;
- large Skyseed topology sizing for syncgroups, reorder routes, and queue ids.

## Commands

Generate the summary:

```bash
python satellite/apps/probe_theaterq_timeline.py --format text
```

Verification:

```bash
python -m py_compile satellite/apps/probe_theaterq_timeline.py
python -m unittest satellite/test_theaterq_timeline_probe.py
git diff --check
```
