"""Reusable Linux link, qdisc, address and route netlink mechanisms."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, IPv4Interface
import os
import socket
import struct
from threading import local


_CALLS = local()


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

    def ipv4_route_inventory(self) -> tuple[tuple[int, IPv4Network], ...]:
        """One preparation snapshot, including table identity and default routes."""
        routes = []
        # RT_TABLE_UNSPEC and an unfiltered dump include all routing tables.
        payload = struct.pack("=BBBBBBBBI", socket.AF_INET, 0, 0, 0, 0, 0, 0, 0, 0)
        for kind, body in self.request(26, payload, flags=0x300):  # RTM_GETROUTE
            if kind != 24 or len(body) < 12 or body[0] != socket.AF_INET or body[1] > 32:
                raise RuntimeError("invalid IPv4 route inventory record")
            attrs = _inventory_attributes(body, 12)
            address = attrs.get(1, bytes(4) if body[1] == 0 else None)
            if address is None or len(address) != 4:
                raise RuntimeError("IPv4 route inventory omitted a destination")
            table = struct.unpack('=I', attrs.get(15, struct.pack('=I', body[4])))[0]
            routes.append((table, IPv4Network((int.from_bytes(address, 'big'), body[1]), strict=False)))
        return tuple(routes)

    def ipv4_route_networks(self) -> tuple[IPv4Network, ...]:
        return tuple(network for _table, network in self.ipv4_route_inventory() if network.prefixlen)

    def link_index(self, name: str) -> int:
        records = self.request(18, _ifinfo() + _text(3, name))
        if len(records) != 1 or records[0][0] != 16:
            raise RuntimeError("RTM_GETLINK omitted the requested interface")
        index = struct.unpack_from("=i", records[0][1], 4)[0]
        if index <= 0:
            raise RuntimeError("RTM_GETLINK returned an invalid ifindex")
        return index

    def initial_links(self, *, reserved_prefix: str, group_start: int, group_end: int) -> int:
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
            if name.startswith(reserved_prefix) or group_start < group <= group_end:
                raise RuntimeError("Runner interface or retirement group already exists")
            if name == "lo":
                loopback = struct.unpack_from("=i", body, 4)[0]
        if loopback is None:
            raise RuntimeError("startup inventory omitted loopback")
        return loopback

    def create_veth(self, name: str, peer: str, peer_netns_fd: int, *,
                    mac: str | None = None, group: int = 0, peer_group: int = 0) -> None:
        address = b"" if mac is None else _attr(1, bytes.fromhex(mac.replace(":", "")))
        queues = _u32(31, 1) + _u32(32, 1)  # IFLA_NUM_TX/RX_QUEUES
        peer_info = (_ifinfo() + _text(3, peer) + _u32(28, peer_netns_fd)
                     + address + queues + _u32(27, peer_group))
        data = _attr(1, peer_info)  # VETH_INFO_PEER
        info = _text(1, "veth") + _attr(2 | 0x8000, data)
        self.request(16, _ifinfo() + _text(3, name) + address + queues
                     + _u32(27, group) + _attr(18 | 0x8000, info), flags=0x400 | 0x200)

    def create_dummy(self, name: str) -> None:
        info = _text(1, "dummy")
        self.request(16, _ifinfo() + _text(3, name) + _attr(18 | 0x8000, info), flags=0x400 | 0x200)

    def native_ipv4(self, index: int) -> None:
        # IFLA_AF_SPEC / AF_INET / IFLA_INET_CONF, indexed IPv4 devconf.
        # Only owned interfaces change; global and SEED interface settings remain intact.
        conf = b"".join(_u32(key, value) for key, value in ((1, 1), (4, 0), (6, 0), (8, 0)))
        inet = _attr(1 | 0x8000, conf)
        af = _attr(socket.AF_INET | 0x8000, inet)
        self.request(16, _ifinfo(index) + _attr(26 | 0x8000, af))

    def local_ingress_ipv4(self, index: int) -> None:
        # Fixed injection enters with an address owned by this same node.
        # Only the owned ingress changes: forwarding, redirects, rp_filter,
        # accept_local. Never alter all/default or ordinary SEED interfaces.
        conf = b"".join(_u32(key, value) for key, value in ((1, 1), (4, 0), (6, 0), (8, 0), (23, 1)))
        self.request(16, _ifinfo(index) + _attr(26 | 0x8000,
            _attr(socket.AF_INET | 0x8000, _attr(1 | 0x8000, conf))))

    def audit_local_ingress(self, table: int, priorities: tuple[int, ...],
                            routes: tuple[tuple[int, IPv4Network], ...]) -> None:
        """Require the ordinary local rule and unused owned RPDB/table slots."""
        header = struct.pack('=BBBBBBBBI', socket.AF_INET, 0, 0, 0, 0, 0, 0, 0, 0)
        local_rules = 0
        for kind, body in self.request(34, header, flags=0x300):  # RTM_GETRULE
            if kind != 32 or len(body) < 12 or body[0] != socket.AF_INET:
                raise RuntimeError('invalid IPv4 rule inventory')
            attrs = _inventory_attributes(body, 12)
            priority = struct.unpack('=I', attrs.get(6, bytes(4)))[0]
            selected = struct.unpack('=I', attrs.get(15, struct.pack('=I', body[4])))[0]
            if selected == table or priority in priorities:
                raise RuntimeError('BPF local ingress rule/table already in use')
            if priority == 0:
                if (body[1:4] != bytes(3) or body[7] != 1 or selected != 255
                        or body[8:12] != bytes(4) or set(attrs) - {6, 14, 15, 21}
                        or attrs.get(14, b'\xff' * 4) != b'\xff' * 4):
                    raise RuntimeError('BPF local ingress requires the ordinary priority-zero local rule')
                local_rules += 1
        if local_rules != 1:
            raise RuntimeError('BPF local ingress requires one priority-zero local rule')
        if any(selected == table for selected, _network in routes):
            raise RuntimeError('BPF local ingress route table already in use')

    def local_output_rule(self, destination: str, *, table: int, priority: int,
                          protocol: int, delete: bool = False) -> None:
        network = IPv4Network(destination)
        payload = struct.pack('=BBBBBBBBI', socket.AF_INET, network.prefixlen, 0, 0, 0, 0, 0, 1, 0)
        payload += (_attr(1, network.network_address.packed) + _text(3, 'lo')
                    + _u32(6, priority) + _u32(15, table) + _attr(21, bytes([protocol])))
        self.request(33 if delete else 32, payload, flags=0 if delete else 0x400 | 0x200)

    def local_output_route(self, index: int, destination: str, *, table: int,
                           gateway: str, prefsrc: str, protocol: int, delete: bool = False) -> None:
        network = IPv4Network(destination)
        # An on-link fixed gateway needs one neighbour, regardless of the
        # number of business identities. No address is assigned to the veth.
        payload = struct.pack('=BBBBBBBBI', socket.AF_INET, network.prefixlen, 0, 0, 0, protocol, 0, 1, 4)
        payload += (_attr(1, network.network_address.packed) + _u32(15, table)
                    + _u32(4, index) + _attr(5, IPv4Address(gateway).packed)
                    + _attr(7, IPv4Address(prefsrc).packed))
        self.request(25 if delete else 24, payload, flags=0 if delete else 0x400 | 0x200)

    def set_link(self, index: int, *, up: bool | None = None, mac: str | None = None) -> None:
        payload = _ifinfo(index, int(bool(up)), int(up is not None))
        if mac is not None:
            payload += _attr(1, bytes.fromhex(mac.replace(":", "")))
        self.request(16, payload)

    def delete_link(self, index: int) -> None:
        self.request(17, _ifinfo(index))

    def delete_group(self, group: int) -> None:
        if type(group) is not int or not 0 < group <= 0xFFFFFFFF:
            raise ValueError("deletion requires a nonzero group")
        # No index/name: rtnl_group_dellink queues this namespace's group into
        # one unregister_netdevice_many call, including implicit veth peers.
        self.request(17, _ifinfo() + _u32(27, group))

    def address(self, index: int, address: str, *, delete: bool = False, exclusive: bool = False) -> None:
        interface = IPv4Interface(address)
        raw = interface.ip.packed
        payload = struct.pack("=BBBBI", socket.AF_INET, interface.network.prefixlen, 0, 0, index)
        payload += _attr(1, raw) + _attr(2, raw)
        self.request(21 if delete else 20, payload, flags=0 if delete else 0x400 | (0x200 if exclusive else 0x100))

    def neighbour(self, index: int, destination: str, mac: str, *, delete: bool = False) -> None:
        payload = struct.pack("=BBHiHBB", socket.AF_INET, 0, 0, index, 0x80, 0, 0)
        payload += _attr(1, IPv4Address(destination).packed)
        if not delete:
            payload += _attr(2, bytes.fromhex(mac.replace(":", "")))
        self.request(29 if delete else 28, payload, flags=0 if delete else 0x400 | 0x100)

    def native_route(self, destination: str, *, kind: str, protocol: int, priority: int,
                     index: int | None = None, gateway: str | None = None,
                     prefsrc: str | None = None, delete: bool = False) -> None:
        # Main table, /32, TOS 0, universe scope. Exclusive add never replaces an
        # existing exact key. Delete includes ownership and all forwarding fields.
        if not 0 < protocol < 256 or not 0 < priority <= 0xFFFFFFFF:
            raise ValueError("native route ownership requires nonzero protocol and metric")
        route_type = {"unicast": 1, "blackhole": 6}[kind]
        payload = struct.pack("=BBBBBBBBI", socket.AF_INET, 32, 0, 0, 254, protocol, 0, route_type, 0)
        payload += _attr(1, IPv4Address(destination).packed) + _u32(6, priority)
        if kind == "unicast":
            if index is None or index <= 0 or gateway is None or prefsrc is None:
                raise ValueError("native unicast route requires interface, gateway and preferred source")
            payload += _u32(4, index) + _attr(5, IPv4Address(gateway).packed) + _attr(7, IPv4Address(prefsrc).packed)
        elif index is not None or gateway is not None or prefsrc is not None:
            raise ValueError("native blackhole route cannot carry forwarding fields")
        self.request(25 if delete else 24, payload, flags=0 if delete else 0x400 | 0x200)

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
