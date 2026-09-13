"""Private Linux rtnetlink UAPI transport over retained namespace sockets.

Constants and layouts follow linux/{rtnetlink,if_link,veth,pkt_sched,pkt_cls}.h.
Every mutation requests an ACK; no command-line networking tools are involved.
"""
from __future__ import annotations

import errno
from ipaddress import IPv4Address, IPv4Network
import os
import socket
import struct
from threading import local


_CALLS = local()
# Private per-namespace retirement IDs. Zero is reserved for non-owner peers.
_RETIREMENT_GROUP_BASE = 0x53530000


def _request_count() -> int:
    return getattr(_CALLS, "requests", 0)


def _attr(kind: int, payload: bytes) -> bytes:
    length = len(payload) + 4
    return struct.pack("=HH", length, kind) + payload + bytes((-length) % 4)


def _u32(kind: int, value: int) -> bytes:
    return _attr(kind, struct.pack("=I", value))


def _text(kind: int, value: str) -> bytes:
    return _attr(kind, value.encode() + b"\0")


def _ifinfo(index: int = 0, flags: int = 0, change: int = 0) -> bytes:
    return struct.pack("=BBHiII", socket.AF_UNSPEC, 0, 0, index, flags, change)


def _inventory_attributes(body: bytes, offset: int) -> dict[int, bytes]:
    attrs = {}
    while offset < len(body):
        if len(body) - offset < 4:
            raise RuntimeError("truncated IPv4 inventory attribute")
        length, key = struct.unpack_from("=HH", body, offset)
        if length < 4 or offset + length > len(body):
            raise RuntimeError("invalid IPv4 inventory attribute")
        attrs[key & 0x3FFF] = body[offset + 4:offset + length]
        offset += (length + 3) & ~3
    return attrs


class _RouteSocket:
    """One synchronous socket, created once inside a retained net namespace."""

    def __init__(self) -> None:
        self._socket = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE)
        self._socket.bind((0, 0))
        self._sequence = 0

    def close(self) -> None:
        self._socket.close()

    def request(self, kind: int, payload: bytes, *, flags: int = 0) -> list[tuple[int, bytes]]:
        self._sequence += 1
        sequence = self._sequence
        packet = struct.pack("=IHHII", 16 + len(payload), kind, 1 | 4 | flags, sequence, 0) + payload
        _CALLS.requests = _request_count() + 1
        self._socket.sendto(packet, (0, 0))
        records = []
        while True:
            packet, _ancillary, message_flags, sender = self._socket.recvmsg(1 << 20)
            if not packet:
                raise RuntimeError("rtnetlink result ended before completion")
            if message_flags & socket.MSG_TRUNC:
                raise RuntimeError("truncated rtnetlink result")
            if sender[0] != 0:
                raise RuntimeError("rtnetlink response is not from the kernel")
            offset = 0
            while offset < len(packet):
                if len(packet) - offset < 16:
                    raise RuntimeError("truncated rtnetlink header")
                length, response_kind, response_flags, response_sequence, _pid = struct.unpack_from("=IHHII", packet, offset)
                if length < 16 or offset + length > len(packet) or response_sequence != sequence:
                    raise RuntimeError("invalid rtnetlink response framing")
                body = packet[offset + 16:offset + length]
                offset += (length + 3) & ~3
                if response_flags & 0x10:
                    raise RuntimeError("interrupted rtnetlink dump")
                if response_kind in (2, 3):  # NLMSG_ERROR / NLMSG_DONE
                    if len(body) < 4:
                        raise RuntimeError("truncated rtnetlink status")
                    code = struct.unpack_from("=i", body)[0]
                    if code:
                        raise OSError(-code, os.strerror(-code))
                    if response_kind == 2 and flags & 0x300 == 0x300:
                        # A successful ACK is not the end of a multipart dump.
                        continue
                    return records
                if response_kind == 4:
                    raise RuntimeError("rtnetlink inventory overrun")
                if response_kind == 1:  # NLMSG_NOOP
                    continue
                records.append((response_kind, body))

    def ipv4_address_facts(self) -> tuple[tuple[IPv4Address, int], ...]:
        facts = []
        payload = struct.pack("=BBBBI", socket.AF_INET, 0, 0, 0, 0)
        for kind, body in self.request(22, payload, flags=0x300):  # RTM_GETADDR
            if kind != 20 or len(body) < 8 or body[0] != socket.AF_INET or body[1] > 32:
                raise RuntimeError("invalid IPv4 address inventory record")
            attrs = _inventory_attributes(body, 8)
            # IFA_ADDRESS is the peer on point-to-point interfaces. IFA_LOCAL
            # is the local address, as exposed by ip -j address's local field.
            address = attrs.get(2, attrs.get(1))
            if address is None or len(address) != 4:
                raise RuntimeError("IPv4 address inventory omitted a local address")
            facts.append((IPv4Address(address), body[1]))
        return tuple(facts)

    def ipv4_route_networks(self) -> tuple[IPv4Network, ...]:
        networks = []
        # RT_TABLE_UNSPEC and an unfiltered dump include all routing tables.
        payload = struct.pack("=BBBBBBBBI", socket.AF_INET, 0, 0, 0, 0, 0, 0, 0, 0)
        for kind, body in self.request(26, payload, flags=0x300):  # RTM_GETROUTE
            if kind != 24 or len(body) < 12 or body[0] != socket.AF_INET or body[1] > 32:
                raise RuntimeError("invalid IPv4 route inventory record")
            attrs = _inventory_attributes(body, 12)
            if body[1] == 0:
                continue
            address = attrs.get(1)  # RTA_DST, including local/broadcast routes
            if address is None or len(address) != 4:
                raise RuntimeError("IPv4 route inventory omitted a destination")
            networks.append(IPv4Network((int.from_bytes(address, 'big'), body[1]), strict=False))
        return tuple(networks)

    def link_index(self, name: str) -> int:
        records = self.request(18, _ifinfo() + _text(3, name))
        if len(records) != 1 or records[0][0] != 16:
            raise RuntimeError("RTM_GETLINK omitted the requested interface")
        index = struct.unpack_from("=i", records[0][1], 4)[0]
        if index <= 0:
            raise RuntimeError("RTM_GETLINK returned an invalid ifindex")
        return index

    def initial_links(self, group_end: int) -> int:
        """One startup inventory supplies loopback identity and collision checks."""
        loopback = None
        for kind, body in self.request(18, _ifinfo(), flags=0x300):
            if kind != 16:
                raise RuntimeError("unexpected startup link inventory record")
            attrs = {}
            offset = 16
            while offset < len(body):
                length, key = struct.unpack_from("=HH", body, offset)
                if length < 4 or offset + length > len(body):
                    raise RuntimeError("invalid startup link attribute")
                attrs[key & 0x3FFF] = body[offset + 4:offset + length]
                offset += (length + 3) & ~3
            name = attrs.get(3, b"").rstrip(b"\0").decode()
            group = struct.unpack("=I", attrs.get(27, bytes(4)))[0]
            if name.startswith("sv2") or _RETIREMENT_GROUP_BASE < group <= group_end:
                raise RuntimeError("Runner interface or retirement group already exists")
            if name == "lo":
                loopback = struct.unpack_from("=i", body, 4)[0]
        if loopback is None:
            raise RuntimeError("startup inventory omitted loopback")
        return loopback

    def create_veth(self, name: str, peer: str, peer_netns_fd: int, *,
                    mac: str, group: int, peer_group: int) -> None:
        address = _attr(1, bytes.fromhex(mac.replace(":", "")))
        queues = _u32(31, 1) + _u32(32, 1)  # IFLA_NUM_TX/RX_QUEUES
        peer_info = (_ifinfo() + _text(3, peer) + _u32(28, peer_netns_fd)
                     + address + queues + _u32(27, peer_group))
        data = _attr(1, peer_info)  # VETH_INFO_PEER
        info = _text(1, "veth") + _attr(2 | 0x8000, data)
        self.request(16, _ifinfo() + _text(3, name) + address + queues
                     + _u32(27, group) + _attr(18 | 0x8000, info), flags=0x400 | 0x200)

    def set_link(self, index: int, *, up: bool | None = None, mac: str | None = None) -> None:
        payload = _ifinfo(index, int(bool(up)), int(up is not None))
        if mac is not None:
            payload += _attr(1, bytes.fromhex(mac.replace(":", "")))
        self.request(16, payload)

    def delete_link(self, index: int) -> None:
        self.request(17, _ifinfo(index))

    def delete_group(self, group: int) -> None:
        if type(group) is not int or not _RETIREMENT_GROUP_BASE < group <= 0xFFFFFFFF:
            raise ValueError("deletion requires a nonzero owned retirement group")
        # No index/name: rtnl_group_dellink queues this namespace's group into
        # one unregister_netdevice_many call, including implicit veth peers.
        self.request(17, _ifinfo() + _u32(27, group))

    def address(self, index: int, address: str, *, delete: bool = False) -> None:
        raw = IPv4Address(address).packed
        payload = struct.pack("=BBBBI", socket.AF_INET, 32, 0, 0, index)
        payload += _attr(1, raw) + _attr(2, raw)
        self.request(21 if delete else 20, payload, flags=0 if delete else 0x400 | 0x100)

    def route(self, index: int, destination: str, *, delete: bool = False) -> None:
        # Main table, RTPROT_BOOT, RT_SCOPE_LINK, RTN_UNICAST, exact /32.
        payload = struct.pack("=BBBBBBBBI", socket.AF_INET, 32, 0, 0, 254, 3, 253, 1, 0)
        payload += _attr(1, IPv4Address(destination).packed) + _u32(4, index)
        self.request(25 if delete else 24, payload, flags=0 if delete else 0x400 | 0x100)

    def neighbour(self, index: int, destination: str, mac: str, *, delete: bool = False) -> None:
        payload = struct.pack("=BBHiHBB", socket.AF_INET, 0, 0, index, 0x80, 0, 0)
        payload += _attr(1, IPv4Address(destination).packed)
        if not delete:
            payload += _attr(2, bytes.fromhex(mac.replace(":", "")))
        self.request(29 if delete else 28, payload, flags=0 if delete else 0x400 | 0x100)

    @staticmethod
    def _tc(index: int, handle: int, parent: int, info: int = 0) -> bytes:
        return struct.pack("=BBHiIII", socket.AF_UNSPEC, 0, 0, index, handle, parent, info)

    def netem(self, index: int, parameters: dict) -> None:
        loss = round(int(parameters["packet_loss_ppm"]) * 0xFFFFFFFF / 1_000_000)
        rate = int(parameters["bandwidth_bps"]) // 8
        options = struct.pack("=IIIIII", 0, 1000, loss, 0, 0, 0)
        options += _attr(10, struct.pack("=q", int(parameters["delay_us"]) * 1000))
        options += _attr(6, struct.pack("=IiIi", min(rate, 0xFFFFFFFF), 0, 0, 0))
        if rate >= 1 << 32:
            options += _attr(8, struct.pack("=Q", rate))
        self.request(36, self._tc(index, 0x10000, 0xFFFFFFFF) + _text(1, "netem") + _attr(2, options), flags=0x400 | 0x100)

    def attach_bpf(self, index: int, program_fd: int) -> None:
        self.request(36, self._tc(index, 0xFFFF0000, 0xFFFFFFF1) + _text(1, "clsact"), flags=0x400 | 0x100)
        options = _u32(6, program_fd) + _text(7, "skyseed_tc") + _u32(8, 1)
        self.request(44, self._tc(index, 1, 0xFFFFFFF2, (1 << 16) | socket.htons(0x0800)) + _text(1, "bpf") + _attr(2, options), flags=0x400 | 0x100)


__all__: list[str] = []
