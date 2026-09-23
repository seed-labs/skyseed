"""Opt-in real UAPI check, confined to three anonymous network namespaces."""
from __future__ import annotations

import ctypes
import fcntl
import json
import os
import socket
import struct

import pytest

from skyseed.backend.bpf.dataplane.materials import _RETIREMENT_GROUP_BASE
from skyseed.backend.linux.netlink import _RouteSocket, _attr, _ifinfo, _text, _u32


def _attributes(payload):
    result = {}
    while payload:
        length, kind = struct.unpack_from("=HH", payload)
        result[kind & 0x3FFF] = payload[4:length]
        payload = payload[(length + 3) & ~3:]
    return result


def check_primitives():
    libc = ctypes.CDLL(None, use_errno=True)
    original = os.open("/proc/self/ns/net", os.O_RDONLY)
    namespaces = []
    channels = []
    mac = "02:53:4b:59:00:01"
    group = _RETIREMENT_GROUP_BASE + 1

    def switch(fd):
        if libc.setns(fd, 0x40000000):
            raise OSError(ctypes.get_errno(), "setns")

    def inventory(channel):
        return {attrs[3].rstrip(b"\0").decode(): (body, attrs)
                for _, body in channel.request(18, _ifinfo(), flags=0x300)
                for attrs in (_attributes(body[16:]),)}

    def effective_queues(namespace, name):
        switch(namespace)
        try:
            values = (ctypes.c_uint32 * 9)(0x3C)  # ETHTOOL_GCHANNELS
            request = bytearray(struct.pack("=16sQ16x", name.encode(), ctypes.addressof(values)))
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
                fcntl.ioctl(channel, 0x8946, request)  # SIOCETHTOOL
            return {"rx": values[5], "tx": values[6]}
        finally:
            switch(original)

    try:
        for _ in range(3):
            if libc.unshare(0x40000000):
                raise OSError(ctypes.get_errno(), "unshare")
            namespaces.append(os.open("/proc/self/ns/net", os.O_RDONLY))
            channels.append(_RouteSocket())
            switch(original)
        a, b, c = channels
        # Control uses the preceding request's omitted queue attributes.
        peer = _ifinfo() + _text(3, "controlb") + _u32(28, namespaces[1])
        info = _text(1, "veth") + _attr(2, _attr(1, peer))
        a.request(16, _ifinfo() + _text(3, "controla") + _attr(18, info), flags=0x600)
        _, attrs = inventory(a)["controla"]
        default_allocated = {"rx": struct.unpack("=I", attrs[32])[0],
                             "tx": struct.unpack("=I", attrs[31])[0]}
        default_effective = effective_queues(namespaces[0], "controla")
        assert default_effective == {"rx": 1, "tx": 1}
        a.create_veth("sv2prim001a", "sv2prim001b", namespaces[1], mac=mac, group=group, peer_group=0)
        # The selected owner is the peer endpoint of this second creation.
        b.create_veth("sv2prim002a", "sv2prim002b", namespaces[0], mac=mac, group=0, peer_group=group)
        a.create_veth("sv2futurea", "sv2futureb", namespaces[2], mac=mac, group=group + 1, peer_group=0)
        checked = 0
        for channel, namespace in zip(channels, namespaces):
            for name, (body, attrs) in inventory(channel).items():
                if not name.startswith("sv2"):
                    continue
                assert attrs[1] == bytes.fromhex(mac.replace(":", ""))
                assert struct.unpack("=I", attrs[31]) == (1,)
                assert struct.unpack("=I", attrs[32]) == (1,)
                assert effective_queues(namespace, name) == {"rx": 1, "tx": 1}
                assert not struct.unpack_from("=I", body, 8)[0] & 1
                channel.set_link(struct.unpack_from("=i", body, 4)[0], up=True)
                checked += 1
        a.set_link(a.link_index("controla"), up=True)
        b.set_link(b.link_index("controlb"), up=True)
        a.delete_group(group)
        remaining = [set(inventory(channel)) for channel in channels]
        assert remaining == [{"lo", "controla", "sv2futurea"}, {"lo", "controlb"}, {"lo", "sv2futureb"}]
        a.delete_link(a.link_index("controla"))  # Direct deletion of an UP pair.
        assert "controlb" not in inventory(b)
        a.delete_group(group + 1)
        assert all(set(inventory(channel)) == {"lo"} for channel in channels)
        return {"kernel": os.uname().release, "possible_cpus": open("/sys/devices/system/cpu/possible").read().strip(),
                "default_allocated_queues": default_allocated, "default_effective_queues": default_effective,
                "explicit_allocated_and_effective_queues": {"rx": 1, "tx": 1},
                "mac_and_initial_down_endpoints_checked": checked,
                "group_request_count": 1, "pairs_retired_in_group": 2,
                "implicit_peers_removed": True, "other_group_and_unrelated_pair_preserved": True,
                "direct_up_pair_deletion": True, "owned_residue": 0}
    finally:
        switch(original)
        for channel in channels:
            channel.close()
        for fd in namespaces:
            os.close(fd)
        os.close(original)


@pytest.mark.skipif(os.environ.get("SKYSEED_KERNEL_PRIMITIVES") != "1", reason="requires explicit scoped privileged invocation")
def test_real_runner_kernel_primitives():
    assert check_primitives()["owned_residue"] == 0


if __name__ == "__main__":
    print(json.dumps(check_primitives(), indent=2))
