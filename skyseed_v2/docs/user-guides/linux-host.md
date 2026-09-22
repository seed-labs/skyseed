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

The [H1–H4 history](../history/optimization-decision-history.md#h1) records why this
workstation was adjusted. These values are **not unconditional installation minima**.
Diagnose the resource actually exhausted before changing it; save existing configuration
and preserve unrelated entries. Service restarts belong in a maintenance window after
owned experiments and other affected work have drained.

### Native IPv4 neighbor capacity

Full native S4 uses thousands of neighbor entries across network namespaces. The
host's shared IPv4 ARP-table defaults can be too small even when per-container counts
are modest. Inspect the live values and retain them with the experiment evidence:

```bash
sysctl net.ipv4.neigh.default.gc_thresh1 net.ipv4.neigh.default.gc_thresh2 net.ipv4.neigh.default.gc_thresh3
```

The recorded S4 campaign used **8192 / 16384 / 32768**. These are scene-specific capacity
values, not universal minimums. Where the host administrator approves retaining them,
use the existing `sysctl.d` mechanism before deployment, with no active experiment.
Inspect `/etc/sysctl.conf`, `/etc/sysctl.d`, `/run/sysctl.d`, `/usr/local/lib/sysctl.d`
and `/usr/lib/sysctl.d` where present. Keep larger active or configured values and
unrelated entries; resolve later conflicting assignments explicitly. For these minimums:

```bash
sudoedit /etc/sysctl.d/99-skyseed-neighbor-capacity.conf
```

```ini
net.ipv4.neigh.default.gc_thresh1 = 8192
net.ipv4.neigh.default.gc_thresh2 = 16384
net.ipv4.neigh.default.gc_thresh3 = 32768
```

Apply only the approved file with `sudo sysctl -p /etc/sysctl.d/99-skyseed-neighbor-capacity.conf`,
then read back the live values. Do not restore small defaults after each run on a host
approved for permanent capacity. Live values alone do not establish persistence;
privileged installation and subsequent verification are administrator responsibilities.
Deployer never changes host capacity silently. Do not change it during a live run.
`table_fulls` growth in `/proc/net/stat/arp_cache`, failed neighbor resolution and
`RTM_NEWNEIGH: No buffer space available` are exhaustion evidence, not ordinary OSPF
convergence. Keep daemon/kernel and host-resource evidence separately.

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
object inside it. Container lifecycle monitoring can exhaust per-user instances even
with watches available. Neither count is a process descriptor limit or a memory limit.
The historical instance adjustment was 128 → 1024. Inspect with:

```bash
sysctl fs.inotify.max_user_instances fs.inotify.max_user_watches
```

When instance exhaustion is diagnosed, edit a dedicated file such as
`/etc/sysctl.d/99-skyseed-container-monitoring.conf` with `sudoedit`, setting only
`fs.inotify.max_user_instances = 1024`. Check other sysctl files for later conflicting
assignments, then apply that file with
`sudo sysctl -p /etc/sysctl.d/99-skyseed-container-monitoring.conf` and read the
value again with `sysctl`. More instances permit additional kernel resource use;
raising watches does not solve an instance shortage. See Ubuntu's
[inotify manual](https://manpages.ubuntu.com/manpages/noble/man7/inotify.7.html).

### D-Bus per-user connections

The historical system-bus adjustment was 256 → 512 after large container startup
exhausted UID 0's connection allowance. It concerns bus clients for lifecycle control,
not satellite links. Inspect the service and configured limits:

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

This workstation changed from systemd to cgroupfs after large-scale transient
`docker-*.scope` lifecycle timeouts, retaining cgroup v2. That changes who manages
container cgroups; CPU/memory/PID isolation remains. It reduces integration with
per-container systemd scopes. Docker normally defaults to systemd on cgroup v2;
cgroupfs is a diagnosed workaround here, not a universal recommendation.
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
/var/lib/docker`). The historical NVMe/bind-mount layout is specific to this workstation.
No particular device or mount path is required. Moving Docker/containerd data requires
its own migration and service-ordering plan; it is not part of installing SkySEED.

## Read-only workstation observation: 18 September 2026

This is a new inspection, not a claim that every setting was captured during the
previous six-example campaign. No settings or mounts were changed for this check.

| Item | Observed value |
|---|---|
| Host | Ubuntu 24.04.4; Linux 7.0.0-31-generic; x86_64; 32 logical CPUs; 60.5 GiB RAM |
| Docker / Compose plugin | Engine 29.6.2; Compose v5.3.1 (`docker compose`) |
| Cgroups | cgroupfs driver, cgroup v2 |
| inotify | 1,024 instances/user; 65,536 watches/user, effective sysctl values |
| System-bus file | `max_connections_per_user=512`; configured value, no live internal-limit query |
| Docker service descriptor hard limit | `LimitNOFILE=524288`; actual helper limits must be inspected separately |
| Host collector library | Ubuntu `libbpf1` 1:1.3.0-2build2 |
| Storage | NVMe ext4 bind mounts for Docker, containerd and the user's home; bpffs mounted |

The [scale reference](scale-and-resources.md) reports the prior campaign's timings
and shared-host memory observations under their original conditions.
