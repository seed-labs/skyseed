# Linux host for network execution

Python calculation and browser viewing use the [getting-started setup](getting-started.md).
Deployment and Runner execution additionally need a local Linux Docker/BPF host.
The configuration below separates required capabilities from adjustments motivated
by scale. The [resource reference](scale-and-resources.md) describes measured scenes.

## Required capabilities and permissions

- Use a Linux kernel with network namespaces, veth, netem, clsact/tc BPF, BPF hash/array
  maps and ring buffers, with writable bpffs at `/sys/fs/bpf`. Raw BPF syscall dispatch
  currently supports x86_64 and aarch64; the recorded live validation is x86_64.
  A CPU architecture entry is not qualification of every image/kernel combination.
- Use a **local, rootful Docker Engine** and the current `docker compose` plugin.
  Runner's owned helper uses privileged mode, host PID/network namespaces, a read-only
  host-root bind and a writable bpffs bind. Docker access must permit these operations;
  rootless Docker and a remote daemon do not provide this execution arrangement.
- Keep the selected Python environment and SkySEED source accessible on this host.
  The helper enters the host filesystem with `chroot /host` and runs that exact Python
  executable. The Traffic collector loads **host `libbpf.so.1`** there, even though
  its process is launched through the helper container.
- Allow image retrieval/builds and sufficient disk, memory and descriptor capacity.
  Generated SEED images provide node tools such as `iproute2` and `iputils-ping`;
  ordinary ground routers also receive their SEED routing software. These image
  packages are not a shopping list of host requirements.

On Ubuntu 24.04, configure Docker's apt repository using its
[official Ubuntu installation instructions](https://docs.docker.com/engine/install/ubuntu/),
then install Engine, CLI, containerd, Buildx and the Compose plugin as documented.
Install the host collector library with `sudo apt install libbpf1`; Ubuntu's
[package description](https://packages.ubuntu.com/noble/libbpf1) identifies this runtime library.
The current Runner emits BPF instructions and uses direct syscalls/netlink; it does
not require a host clang toolchain or bpftool for ordinary execution. Those may be
useful diagnostic tools. No Compose-v1 or BuildKit-disabling workaround is required.

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
findmnt -t bpf /sys/fs/bpf
python -c 'import ctypes; ctypes.CDLL("libbpf.so.1"); print("host libbpf load: OK")'
```

If the kernel lacks a capability or host policy refuses privileged helpers, resolve
that with the host administrator. If bpffs is absent on an otherwise capable Linux
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

The [privileged helper](../../src/skyseed_v2/backend/bpf/runner/privileged_runtime_command.py)
reserves `8 * node_count + 6 * worker_count + 64` descriptors for retained namespaces,
sockets/maps and workers. It raises its own soft `RLIMIT_NOFILE` up to the available
hard limit and fails explicitly if that hard limit is insufficient. These are the
helper process's limits inherited from its Docker container. Raising an unrelated
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
