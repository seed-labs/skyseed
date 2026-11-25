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


class RouteSocket:
    """One synchronous socket, created once inside a retained net namespace."""

    def __init__(self) -> None:
        self._socket = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE)
        self._socket.setsockopt(270, 11, 1)  # NETLINK_EXT_ACK
        self._socket.setsockopt(270, 10, 1)  # NETLINK_CAP_ACK
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
                        detail = ""
                        if response_flags & 0x200:
                            attributes = _inventory_attributes(body, 20)
                            detail = attributes.get(1, b"").rstrip(b"\0").decode(errors="replace")
                        raise OSError(-code, f"{os.strerror(-code)}: {detail}")
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

    def set_link(self, index: int, *, up: bool | None = None, mac: str | None = None,
                 mtu: int | None = None) -> None:
        payload = _ifinfo(index, int(bool(up)), int(up is not None))
        if mac is not None:
            payload += _attr(1, bytes.fromhex(mac.replace(":", "")))
        if mtu is not None:
            payload += _u32(4, mtu)
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


    @staticmethod
    def _tc(index: int, handle: int, parent: int, info: int = 0) -> bytes:
        return struct.pack("=BBHiIII", socket.AF_UNSPEC, 0, 0, index, handle, parent, info)

    def route(self, prefix: str, *, index: int | None = None, gateway: str | None = None,
              source: str | None = None, table: int = 254, protocol: int = 253,
              priority: int = 1, delete: bool = False) -> None:
        network = IPv4Network(prefix)
        route_type = 6 if index is None else 1  # blackhole / unicast
        if delete:
            route_type = 0  # Delete our exact prefix/table/protocol/priority, either route type.
        payload = struct.pack("=BBBBBBBBI", socket.AF_INET, network.prefixlen, 0, 0,
                              table if table < 256 else 0, protocol, 0, route_type, 0)
        payload += _attr(1, network.network_address.packed) + _u32(6, priority) + _u32(15, table)
        if index is not None:
            payload += _u32(4, index)
        if gateway is not None:
            payload += _attr(5, IPv4Address(gateway).packed)
        if source is not None:
            payload += _attr(7, IPv4Address(source).packed)
        self.request(25 if delete else 24, payload, flags=0 if delete else 0x400 | 0x100)

    def netem(self, index: int, parameters: dict) -> None:
        loss = 0xFFFFFFFF if parameters["drop"] else round(parameters["loss_percent"] * 0xFFFFFFFF / 100)
        options = struct.pack("=IIIIII", 0, 1000, loss, 0, 0, 0)
        options += _attr(10, struct.pack("=q", round(parameters["delay_us"] * 1000)))
        if parameters["rate_bps"] is not None:
            rate = max(1, parameters["rate_bps"] // 8)
            options += _attr(6, struct.pack("=IiIi", min(rate, 0xFFFFFFFF), 0, 0, 0))
            if rate >= 1 << 32:
                options += _attr(8, struct.pack("=Q", rate))
        self.request(36, self._tc(index, 0x10000, 0xFFFFFFFF) + _text(1, "netem")
                     + _attr(2, options), flags=0x400 | 0x100)

    def local_route(self, index: int, prefix: str, *, delete=False):
        network = IPv4Network(prefix)
        payload = struct.pack("=BBBBBBBBI", socket.AF_INET, network.prefixlen, 0, 0, 255, 253, 254, 2, 0)
        payload += _attr(1, network.network_address.packed) + _u32(4, index)
        self.request(25 if delete else 24, payload, flags=0 if delete else 0x400 | 0x200)

    def attach_label_program(self, index: int, programs: dict[str, int]) -> None:
        """TC's native MPLS actions change the packet shape between BPF stages."""
        self.request(36, self._tc(index, 0xFFFF0000, 0xFFFFFFF1) + _text(1, "clsact"),
                     flags=0x400 | 0x100)
        for priority, name in ((10, "decide"), (20, "needs_push"), (30, "needs_pop")):
            options = _u32(6, programs[name]) + _text(7, name)
            if name == "decide":
                options += _u32(8, 1)  # TCA_BPF_FLAG_ACT_DIRECT
            else:
                pushing = name == "needs_push"
                # tc_gen followed by m_action; PIPE continues to the BPF action.
                mpls = _attr(2, struct.pack("=IIiiii", 0, 0, 3, 0, 0, 2 if pushing else 1))
                mpls += _attr(4, struct.pack("!H", 0x8847 if pushing else 0x0800))
                if pushing:
                    mpls += _u32(5, 16) + _attr(7, bytes([64])) + _attr(8, bytes([1]))
                action_name = "complete_push" if pushing else "complete_pop"
                bpf = _attr(2, struct.pack("=IIiii", 0, 0, 3, 0, 0))
                bpf += _u32(5, programs[action_name]) + _text(6, action_name)
                actions = _attr(1 | 0x8000, _text(1, "mpls") + _attr(2 | 0x8000, mpls))
                actions += _attr(2 | 0x8000, _text(1, "bpf") + _attr(2 | 0x8000, bpf))
                options += _attr(1 | 0x8000, actions)  # TCA_BPF_ACT
            self.request(44, self._tc(index, 1, 0xFFFFFFF2,
                                     priority << 16 | socket.htons(3))
                         + _text(1, "bpf") + _attr(2 | 0x8000, options), flags=0x400 | 0x100)
