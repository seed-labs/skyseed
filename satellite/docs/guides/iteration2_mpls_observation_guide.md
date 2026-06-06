# Iteration 2 MPLS Observation Guide

This note is for the clean Iteration 2 small-scale demo in `satellite/`.

Use it when you want to:

- keep the continuous demo running
- open containers from the visualization/UI yourself
- manually `ping`
- manually `tcpdump`
- manually inspect interface counters or LFIB state

It focuses on `ut1` because that is the easiest path to watch across slices.

## Start The Continuous Demo

Run this on the host:

```bash
python3 satellite/apps/iteration2_satellite_experiment.py \
  --execute-real \
  --continuous-real \
  --runtime-mode fast-demo \
  --loop-count 999 \
  --interval 15 \
  --max-node-workers 2 \
  --observe-flow ut1:as160-host1 \
  --stream-progress
```

Why these settings:

- `--continuous-real`: real slice transitions
- `--runtime-mode fast-demo`: easier for live observation
- `--loop-count 999`: effectively keeps looping
- `--interval 15`: gives you time to watch each slice after the `5-6s` update cost
- `--observe-flow ut1:as160-host1`: keeps one stable observation target
- `--stream-progress`: prints the active slice and current path live

The live output tells you which slice is active and what path `ut1` is using.

## Best Traffic To Watch

From inside `ut1`, use:

```bash
ping -n 10.160.0.72
```

Why `10.160.0.72`:

- it is `as160-host1`
- it uses the special-prefix Internet path
- that path makes slice-to-slice changes easier to see

You can also test the default Internet path with:

```bash
ping -n 10.154.0.129
```

That is `as154-host-new`.

## Slice Path Summary

These are the important `ut1` paths to know.

### Traffic To `as154-host-new` (`10.154.0.129`)

- `small-scale-slice-0001`: `ut1 -> sat-b1 -> gw1 -> as154-host-new`
- `small-scale-slice-0002`: `ut1 -> sat-b1 -> gw1 -> as154-host-new`
- `small-scale-slice-0003`: `ut1 -> sat-a2 -> gw1 -> as154-host-new`
- `small-scale-slice-0004`: `ut1 -> sat-a2 -> gw2 -> as154-host-new`
- `small-scale-slice-0005`: `ut1 -> sat-a1 -> gw1 -> as154-host-new`

### Traffic To `as160-host1` (`10.160.0.72`)

- `small-scale-slice-0001`: `ut1 -> sat-b1 -> gw2 -> as160-host1`
- `small-scale-slice-0002`: `ut1 -> sat-b1 -> gw2 -> as160-host1`
- `small-scale-slice-0003`: `ut1 -> sat-a2 -> sat-b1 -> gw2 -> as160-host1`
- `small-scale-slice-0004`: the AS160 traffic collapses onto the same direct `gw2` egress as the default path
- `small-scale-slice-0005`: `ut1 -> sat-a1 -> sat-a2 -> gw2 -> as160-host1`

For manual MPLS watching, the clearest slices are:

- `small-scale-slice-0003` for the cross-plane `xp0a/xp0b` hop
- `small-scale-slice-0005` for the same-plane `link-a1-a2` hop

Because those paths are visibly multi-hop:

- `small-scale-slice-0003`: `ut1 -> sat-a2 -> sat-b1 -> gw2 -> as160-host1`
- `small-scale-slice-0005`: `ut1 -> sat-a1 -> sat-a2 -> gw2 -> as160-host1`

## Which Containers And Interfaces Matter

### Slice `0001`

For `ut1`, watch:

- `sat-b1` on `ga-g1s0`
- `gw1` on `ga-g1s0`
- `gw1` on `ix100`

### Slice `0003`

For `ut1`, watch:

- `sat-a2` on `ga-g1s0` for the default path
- `sat-a2` on `xp0a` for the AS160 override
- `sat-b1` on `xp0b` for the AS160 override
- `sat-b1` on `ga-g2s0` for the AS160 override
- `gw2` on `ga-g2s0`
- `gw2` on `ix101`

### Slice `0004`

For `ut1`, this is the cleanest direct `gw2` slice. Watch:

- `sat-a2` on `ga-g2s0`
- `gw2` on `ga-g2s0`
- `gw2` on `ix101`

### Slice `0005`

For `ut1`, this is the best `link-a1-a2` MPLS slice. Watch:

- `sat-a1` on `link-a1-a2`
- `sat-a2` on `link-a1-a2`
- `sat-a2` on `ga-g2s0`
- `gw2` on `ga-g2s0`
- `gw2` on `ix101`

## Manual Commands Inside Containers

These commands assume you already entered the container from the visualization/UI.

### 1. Continuous Ping From `ut1`

Inside `ut1`:

```bash
while true; do
  printf "%s " "$(date +%s.%3N)"
  ping -n -c 1 -W 1 10.160.0.72 | awk '/bytes from|100% packet loss/'
  sleep 0.2
done
```

Use this to estimate recovery time when the slice changes.

### 2. Watch Interface Packet Counters

Inside a satellite or gateway container:

```bash
watch -n 1 'ip -s link show dev link-a1-a2 | sed -n "1,6p"'
```

Examples:

- in `sat-a2`: `watch -n 1 'ip -s link show dev link-a1-a2 | sed -n "1,6p"'`
- in `sat-a1`: `watch -n 1 'ip -s link show dev link-a1-a2 | sed -n "1,6p"'`
- in `sat-a1`: `watch -n 1 'ip -s link show dev ga-g1s0 | sed -n "1,6p"'`
- in `gw1`: `watch -n 1 'ip -s link show dev ix100 | sed -n "1,6p"'`

When the path moves, packet counters should stop increasing on the old path and start increasing on the new one.

### 3. Observe MPLS LFIB State

Inside a satellite or gateway:

```bash
ip -f mpls route show
```

If you want to refresh it repeatedly:

```bash
watch -n 1 'ip -f mpls route show'
```

This shows the LFIB entries currently programmed for the active slice.

### 4. Capture MPLS Packets

Inside the most interesting satellites:

```bash
tcpdump -n -i link-a1-a2 -vv mpls
```

Good places:

- `sat-a2` on `link-a1-a2`
- `sat-a1` on `link-a1-a2`

You can also capture on the satellite-to-gateway edge:

```bash
tcpdump -n -i ga-g1s0 -vv 'mpls or icmp'
```

Examples:

- in `sat-a1`: `tcpdump -n -i ga-g1s0 -vv 'mpls or icmp'`
- in `gw1`: `tcpdump -n -i ga-g1s0 -vv 'mpls or icmp'`

### 5. Observe Internet Egress

Inside `gw1`:

```bash
tcpdump -n -i ix100 -vv icmp
```

Inside `gw2`:

```bash
tcpdump -n -i ix101 -vv icmp
```

This is useful when you want to confirm which gateway actually exits to the Mini Internet.

## What You Should Expect To See

### Direct Path Example

In slices `0001` and `0002`, traffic from `ut1` to `as160-host1` should mainly show up on:

- `sat-b1:ga-g2s0`
- `gw2:ga-g2s0`
- `gw2:ix101`

### Multi-Hop Example

In slice `0005`, traffic from `ut1` to `as160-host1` should show up on:

- `sat-a1:link-a1-a2`
- `sat-a2:link-a1-a2`
- `sat-a2:ga-g2s0`
- `gw2:ga-g2s0`
- `gw2:ix101`

That is the clearest proof that packets moved from a direct path to a multi-hop satellite path.

## MPLS Visibility Notes

What is usually visible:

- MPLS traffic on inter-satellite links such as `link-a1-a2`
- MPLS traffic on satellite-to-gateway interfaces such as `ga-g1s0`
- LFIB entries via `ip -f mpls route show`

What is usually not the best MPLS observation point:

- `ut1` access interface
- gateway Internet uplink (`ix100`, `ix101`)

At those points you mainly observe normal IP/ICMP, because MPLS labels are not carried all the way to the Internet-facing side.

## A Simple Manual Observation Workflow

1. On the host, start the continuous demo with `--stream-progress`.
2. In `ut1`, run the continuous ping to `10.160.0.72`.
3. In `sat-a2`, watch `xp0a`, `ga-g2s0`, or `link-a1-a2` depending on the active slice.
4. In `sat-a1` or `sat-b1`, watch the corresponding inter-satellite hop for the current slice.
5. In `gw2`, watch `ga-g2s0` or `ix101`.
6. Wait for the live progress output to say the slice changed.
7. Check whether counters or packets disappear from the old path and appear on the new path.

## If You Want The Clearest Demonstration

The easiest “show me the path changed” sequence is:

- watch `ut1 -> as160-host1`
- wait for `slice 0003`
- then `slice 0005`
- compare:
  - `slice 0003`: traffic uses the cross-plane hop `sat-a2 -> sat-b1 -> gw2`
  - `slice 0005`: traffic moves onto the same-plane hop `sat-a1 -> sat-a2 -> gw2`

That transition is usually the most convincing MPLS/satellite-path change in this demo.
