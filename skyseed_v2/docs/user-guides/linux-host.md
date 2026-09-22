# Linux host for network execution

Python calculation and browser viewing use the [getting-started setup](getting-started.md).
BPF Compiler preparation also needs the [C/ELF dependencies](../development/dataplane.md#dependencies-and-validation-boundary).
Deployment and Runner execution in either mode need a local Linux/Docker host.
The configuration below separates required capabilities from adjustments motivated
by scale. The [resource reference](scale-and-resources.md) describes measured scenes.

## Required capabilities and permissions

- Both modes require Linux network namespaces, veth, netem and IPv4 netlink operations.
  BPF additionally requires clsact/tc BPF, hash/array maps, ring buffers and writable
  bpffs at `/sys/fs/bpf`. Native Linux execution requires no BPF program/toolchain.
  Raw BPF syscall dispatch
  currently supports x86_64 and aarch64; the recorded live validation is x86_64.
  A CPU architecture entry is not qualification of every image/kernel combination.
- Use a **local, rootful Docker Engine** and the current `docker compose` plugin.
  Runner's owned helper uses privileged mode, host PID/network namespaces, a read-only
  host-root bind; BPF also uses a writable bpffs bind. Docker access must permit these operations;
  rootless Docker and a remote daemon do not provide this execution arrangement.
- Keep the selected Python environment and installed SkySEED package accessible on this host.
  The helper enters the host filesystem with `chroot /host` and runs that exact Python
  executable. The BPF object loader and Traffic collector load **host `libbpf.so.1`** there, even though
  its process is launched through the helper container.
- Allow image retrieval/builds and sufficient disk, memory and descriptor capacity.
  Generated SEED images provide node tools such as `iproute2` and `iputils-ping`;
  ordinary ground routers also receive their SEED routing software. These image
  packages are not a shopping list of host requirements.

On Ubuntu 24.04, configure Docker's apt repository using its
[official Ubuntu installation instructions](https://docs.docker.com/engine/install/ubuntu/),
then install Engine, CLI, containerd, Buildx and the Compose plugin as documented.
For BPF, install the host loader/collector library with `sudo apt install libbpf1`; Ubuntu's
[package description](https://packages.ubuntu.com/noble/libbpf1) identifies this runtime library.
BPF C material preparation requires Clang/LLVM with BPF support, Linux UAPI headers
and libbpf headers (`clang llvm libbpf-dev libelf-dev linux-libc-dev` on Ubuntu).
It embeds compiled ELF in materials. Compatible object inputs need libbpf for
inspection without Clang. Runner loads that ELF through libbpf and uses direct
syscalls/netlink; execution needs no compiler, author headers or source tree. No Compose-v1 or BuildKit-disabling workaround is required.

Follow [Docker's post-installation guide](https://docs.docker.com/engine/install/linux-postinstall/)
for user access. Docker-group access grants root-level control. Run the generated
controls as the environment's ordinary user with authorized Docker access; running
all Python under `sudo` can select the wrong environment and create root-owned output.
Inspect before running Tiny:

```bash
uname -r
uname -m
docker context show
docker version
docker compose version
docker info --format 'driver={{.CgroupDriver}} cgroup={{.CgroupVersion}} root={{.DockerRootDir}}'
# Additional checks for BPF execution:
findmnt -t bpf /sys/fs/bpf
python -c 'import ctypes; ctypes.CDLL("libbpf.so.1"); print("host libbpf load: OK")'
```

If the kernel lacks a capability or host policy refuses privileged helpers, resolve
that with the host administrator. For BPF, if bpffs is absent on an otherwise capable Linux
host, an administrator can mount it with `sudo mount -t bpf bpf /sys/fs/bpf` and
verify with `findmnt`; persistence follows the distribution's mount configuration.
Do not reinterpret a failed permission or kernel operation as successful execution.
Run the normal Tiny path to check the actual helper, images and cleanup together.

## Scale-dependent adjustments

The [host configuration history](../history/optimization-decision-history.md#h1) records
approvals, reasons and dated workstation observations, including persistence status.
These values are **not unconditional installation minima**.
Diagnose the resource actually exhausted before changing it; save existing configuration
and preserve unrelated entries. Service restarts belong in a maintenance window after
owned experiments and other affected work have drained.

### Approved sysctl settings

Reuse the administrator's existing SkySEED sysctl file. On the recorded workstation,
this is `/etc/sysctl.d/99-skyseed-s3-container-control.conf`, which already owns the
inotify-instance setting. Keep the approved ARP thresholds in that same file; do not
create separate neighbor-capacity or container-monitoring files with duplicate keys.
The approval and current status are recorded in [H5](../history/optimization-decision-history.md#h5).

Before editing, inspect live values and every applicable configuration location:

```bash
sysctl fs.inotify.max_user_instances fs.inotify.max_user_watches \
  net.ipv4.neigh.default.gc_thresh1 net.ipv4.neigh.default.gc_thresh2 \
  net.ipv4.neigh.default.gc_thresh3
for directory in /etc/sysctl.d /run/sysctl.d /usr/local/lib/sysctl.d /usr/lib/sysctl.d /lib/sysctl.d; do
  if [ -d "$directory" ]; then
    rg -n --glob '*.conf' '^[[:space:]]*[^#;[:space:]].*=' "$directory"
  fi
done
rg -n '^[[:space:]]*[^#;[:space:]].*=' /etc/sysctl.conf
cat /etc/sysctl.d/99-skyseed-s3-container-control.conf
```

Inspect active assignments, including wildcard and slash-separated keys, rather than
counting comment matches. Resolve symlink aliases before treating repeated file reads
as duplicate configuration. Check same-name overrides and later assignments under the
installed `sysctl.d(5)` / `sysctl(8)` rules; inspect broad wildcard assignments too.
Keep any larger live or configured value for each target key. Resolve conflicting
assignments without deleting unrelated settings or overwriting complete vendor files.
Save the original file before editing. With no active experiment:

```bash
sudoedit /etc/sysctl.d/99-skyseed-s3-container-control.conf
```

For the approved workstation values, retain the existing inotify entry and add or
update each ARP entry once, using a larger value wherever the inspection requires it:

```ini
fs.inotify.max_user_instances = 1024
net.ipv4.neigh.default.gc_thresh1 = 8192
net.ipv4.neigh.default.gc_thresh2 = 16384
net.ipv4.neigh.default.gc_thresh3 = 32768
```

Preserve every unrelated entry. Leave `max_user_watches` with its existing owner;
it is not one of these additions. Apply only the edited file, inspect its content,
repeat the conflict check and verify live values:

```bash
sudo sysctl -p /etc/sysctl.d/99-skyseed-s3-container-control.conf
cat /etc/sysctl.d/99-skyseed-s3-container-control.conf
sysctl fs.inotify.max_user_instances fs.inotify.max_user_watches \
  net.ipv4.neigh.default.gc_thresh1 net.ipv4.neigh.default.gc_thresh2 \
  net.ipv4.neigh.default.gc_thresh3
```

This sysctl change needs no Docker, D-Bus or host restart. Live values alone do not
establish persistence: the file must be installed, checked for overrides and applied
successfully. If administrator access is unavailable, report persistence as pending
and hand off this exact edit/apply/verification step. Do not substitute a second file
or use `sysctl --system` to reapply unrelated settings. Do not restore small defaults
after each run on a workstation approved for permanent capacity. Deployer does not
change host settings silently.

### Native IPv4 neighbor capacity

Full native S4 uses thousands of neighbor entries across network namespaces. The
host's shared IPv4 ARP-table defaults can be too small even when per-container counts
are modest. Use the [approved sysctl procedure](#approved-sysctl-settings) before
deployment; the recorded capacity values are scene-specific, not universal minima.
`table_fulls` growth in `/proc/net/stat/arp_cache`, failed neighbor resolution and
`RTM_NEWNEIGH: No buffer space available` are exhaustion evidence, not ordinary OSPF
convergence. Keep daemon/kernel and host-resource evidence separately; the incidents
and decisions are recorded in [host history](../history/optimization-decision-history.md#h5).

### Independent host observation

For a large deployment, keep host sampling separate from Runner so observations continue
if activation stalls. Choose an existing archive location:

```bash
while sleep 5; do
  date -Is
  free -h
  cat /proc/pressure/cpu /proc/pressure/memory /proc/pressure/io
  cat /proc/loadavg
done | tee /absolute/archive/host-pressure.log
```

Keep kernel OOM protection and the host's memory-pressure protection enabled. Establish
host-appropriate guardrails before a campaign; the historical S4 thresholds and incidents
are in [S4 evidence](../history/native-s4-experiments.md), not framework defaults.
If resource pressure becomes uncontrolled, use `cancel-runtime`, wait for owned work and
Tools to drain, then `cleanup.sh`. An accepted stop or unavailable endpoint is not cleanup
completion. Do not bypass graceful stops/hooks or prune shared image caches.

### inotify instances, watches and descriptors

An inotify instance is an event-monitor object; a watch is one monitored filesystem
object inside it. Neither count is a process descriptor limit or a memory limit.
Inspect both values using the [shared sysctl procedure](#approved-sysctl-settings).
If instance capacity needs adjustment, update its entry in the existing file;
raising watches does not solve an instance shortage. See Ubuntu's
[inotify manual](https://manpages.ubuntu.com/manpages/noble/man7/inotify.7.html)
and the [recorded instance-exhaustion decision](../history/optimization-decision-history.md#h2).

### D-Bus per-user connections

For an approved system-bus connection-limit change, inspect the service and configured
limits first. The workstation's reason and approval are in
[H1](../history/optimization-decision-history.md#h1):

```bash
systemctl status dbus.service
rg -n 'max_connections_per_user|include' /etc/dbus-1 /usr/share/dbus-1/system.conf
journalctl -u dbus.service -u docker.service -u containerd.service
```

For a `dbus-daemon` system bus whose main configuration includes
`/etc/dbus-1/system-local.conf`, use `sudoedit` to add/update this element **inside
its existing `<busconfig>`**, preserving other policy and limits:

```xml
<limit name="max_connections_per_user">512</limit>
```

The [D-Bus daemon manual](https://dbus.freedesktop.org/doc/dbus-daemon.1.html) describes
these limits and warns that reload is partial. A successful reload alone does not
prove this limit changed. Use a planned host reboot to start the bus with the edited
configuration; confirm the running service, included file and journal afterward.
A configured value is not a queried live internal limit. Other bus implementations
need their own configuration procedure.

### Docker cgroup driver

A cgroup-driver change needs a separate approved maintenance window. The workstation
choice, tradeoffs and observations are in [H3](../history/optimization-decision-history.md#h3);
that workaround is not a universal requirement.
[Docker's cgroup reference](https://docs.docker.com/engine/containers/runmetrics/)
explains driver/version inspection.

If adopting that choice, back up `/etc/docker/daemon.json` when present, then use
`sudoedit` to replace only the `native.cgroupdriver=...` item in `exec-opts` with
`native.cgroupdriver=cgroupfs`. Preserve every other array item and JSON key; create
a minimal JSON object only if no file exists. Validate before a planned restart:

```bash
sudo dockerd --validate --config-file=/etc/docker/daemon.json
# After affected workloads are stopped, in the maintenance window:
sudo systemctl restart docker
docker info --format 'driver={{.CgroupDriver}} cgroup={{.CgroupVersion}}'
```

See the [daemon configuration reference](https://docs.docker.com/reference/cli/dockerd/).
Rollback replaces that one option with its previous value and repeats validation;
it never deletes the whole configuration file.

### Runner's helper descriptor boundary

The [BPF privileged helper](../../src/skyseed_v2/backend/bpf/dataplane/runtime_command.py)
reserves `(10 + object_map_count) * node_count + (8 + object_map_count) * worker_count + 64`
descriptors for retained namespaces, sockets, runtime FDs, libbpf objects and workers.
The ELF declaration supplies `object_map_count`, including private state and globals. It raises its own soft `RLIMIT_NOFILE` up to the available
hard limit and fails explicitly if that hard limit is insufficient. The native Linux
helper also reserves its namespace/socket descriptors before acquisition, under the
[native foundation contract](../development/compiler-and-runner.md#native-ipv4-foundation).
These are the helper process's limits inherited from its Docker container. Raising an unrelated
interactive shell's `ulimit` is not a universal fix.

While a helper is active, find its exact container name in Runner's invocation data.
Inspect `docker exec HELPER_NAME cat /proc/1/limits` and the helper Python PID's
`/proc/PID/limits`; `docker inspect HELPER_NAME --format '{{json .HostConfig.Ulimits}}'`
shows explicit container overrides. If the inherited hard limit is too small,
administrators can configure Docker's `default-ulimits.nofile` soft/hard values for
new containers, preserving other JSON settings. Validate the daemon config, restart
in a maintenance window and verify a newly created helper's actual limits. A null
container override does not mean unlimited. Descriptor allowance does not reserve
memory or raise inotify limits.

### Storage placement

Fast local storage helps image builds, container filesystems and retained records.
Inspect available bytes/inodes and actual mounts (`df -h`, `df -i`, `findmnt -T
/var/lib/docker`). No particular device or mount path is required;
the workstation layout and its evidence limits are in
[H4](../history/optimization-decision-history.md#h4). Moving Docker/containerd data requires
its own migration and service-ordering plan; it is not part of installing SkySEED.
