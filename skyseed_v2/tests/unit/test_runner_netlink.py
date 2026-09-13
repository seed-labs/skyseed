from __future__ import annotations

import errno
import socket
import struct

import pytest

from skyseed_v2.backend.bpf._runner.kernel_netlink import _RouteSocket, _attr


def _attributes(payload):
    result = {}
    while payload:
        length, kind = struct.unpack_from("=HH", payload)
        assert 4 <= length <= len(payload)
        result[kind & 0x3FFF] = payload[4:length]
        payload = payload[(length + 3) & ~3:]
    return result


def _capture():
    channel = _RouteSocket.__new__(_RouteSocket)
    calls = []
    channel.request = lambda kind, payload, **kwargs: calls.append((kind, payload, kwargs))
    return channel, calls


def test_veth_request_targets_retained_peer_namespace_descriptor():
    channel, calls = _capture()
    channel.create_veth("sv20000000000a", "sv20000000000b", 47, mac="02:53:4b:59:00:01", group=0x53530001, peer_group=0)
    kind, payload, flags = calls[0]
    assert kind == 16
    attrs = _attributes(payload[16:])
    assert attrs[3] == b"sv20000000000a\0"
    info = _attributes(attrs[18])
    assert info[1] == b"veth\0"
    peer = _attributes(info[2])[1]
    peer_attrs = _attributes(peer[16:])
    assert peer_attrs[3] == b"sv20000000000b\0"
    assert struct.unpack("=I", peer_attrs[28]) == (47,)
    assert flags["flags"] == 0x600
    for endpoint in (attrs, peer_attrs):
        assert endpoint[1] == bytes.fromhex("02534b590001")
        assert struct.unpack("=I", endpoint[31]) == (1,)
        assert struct.unpack("=I", endpoint[32]) == (1,)
    assert struct.unpack("=I", attrs[27]) == (0x53530001,)
    assert struct.unpack("=I", peer_attrs[27]) == (0,)
    assert struct.unpack_from("=II", payload, 8) == (0, 0)
    assert struct.unpack_from("=II", peer, 8) == (0, 0)


def test_netem_encodes_exact_delay_loss_and_large_rate_uapi():
    channel, calls = _capture()
    channel.netem(31, {"delay_us": 12345, "packet_loss_ppm": 250000, "bandwidth_bps": 80_000_000_000})
    kind, payload, flags = calls[0]
    assert kind == 36
    assert struct.unpack_from("=iIII", payload, 4) == (31, 0x10000, 0xFFFFFFFF, 0)
    attrs = _attributes(payload[20:])
    assert attrs[1] == b"netem\0"
    assert struct.unpack_from("=IIIIII", attrs[2]) == (0, 1000, round(0xFFFFFFFF / 4), 0, 0, 0)
    netem = _attributes(attrs[2][24:])
    assert struct.unpack("=q", netem[10]) == (12345000,)
    assert struct.unpack("=Q", netem[8]) == (10_000_000_000,)


def test_bpf_attach_uses_clsact_and_direct_action_program_fd():
    channel, calls = _capture()
    channel.attach_bpf(31, 47)
    assert [kind for kind, _, _ in calls] == [36, 44]
    attrs = _attributes(calls[1][1][20:])
    assert attrs[1] == b"bpf\0"
    options = _attributes(attrs[2])
    assert struct.unpack("=I", options[6]) == (47,)
    assert struct.unpack("=I", options[8]) == (1,)
    assert struct.unpack_from("=I", calls[1][1], 16)[0] == (1 << 16) | socket.htons(0x0800)


@pytest.mark.parametrize("code", [0, -errno.EPERM, -errno.ENODEV, -errno.ENOSPC])
def test_kernel_ack_status_is_not_suppressed(code):
    from skyseed_v2.backend.bpf._runner import kernel_netlink
    count_before = kernel_netlink._request_count()
    channel = _RouteSocket.__new__(_RouteSocket)
    channel._sequence = 0
    class Wire:
        def sendto(self, payload, address):
            self.request = payload
        def recvmsg(self, size):
            return struct.pack("=IHHIIi", 20, 2, 0, 1, 0, code), [], 0, (0, 0)
    channel._socket = Wire()
    if code:
        with pytest.raises(OSError) as caught:
            channel.set_link(31, up=True)
        assert caught.value.errno == -code
    else:
        channel.set_link(31, up=True)
    header = struct.unpack_from("=IHHII", channel._socket.request)
    assert header[2] & 4  # every mutation requests ACK
    assert kernel_netlink._request_count() == count_before + 1


def test_one_bpf_attachment_counts_two_actual_netlink_requests():
    from skyseed_v2.backend.bpf._runner import kernel_netlink
    channel = _RouteSocket.__new__(_RouteSocket)
    channel._sequence = 0
    class Wire:
        def sendto(self, payload, address): self.sequence = struct.unpack_from("=I", payload, 8)[0]
        def recvmsg(self, size): return struct.pack("=IHHIIi", 20, 2, 0, self.sequence, 0, 0), [], 0, (0, 0)
    channel._socket = Wire()
    before = kernel_netlink._request_count()
    channel.attach_bpf(31, 47)
    assert kernel_netlink._request_count() - before == 2


def test_startup_audit_uses_the_preparation_pool_and_propagates_collisions(monkeypatch):
    from ipaddress import IPv4Network
    from skyseed_v2.backend.bpf._runner import privileged_runtime_command as command
    monkeypatch.setattr(command, "_assert_netns", lambda node: None)
    monkeypatch.setattr(command, "_namespace_ipv4_networks", lambda node: ((), ()))
    request = {"node_address_range": "100.64.0.0/29", "zone_prefixes": (),
               "nodes": [{"key": "a", "node_address": "100.64.0.1"}], "worker_count": 2}
    assert command._audit_node_addresses(request)["audited_node_namespaces"] == 1
    monkeypatch.setattr(command, "_namespace_ipv4_networks", lambda node: ((IPv4Network("100.64.0.0/29"),), ()))
    with pytest.raises(RuntimeError, match="collides"):
        command._audit_node_addresses(request)


def test_group_delete_is_one_unqualified_dellink_for_nonzero_owned_group():
    channel, calls = _capture()
    channel.delete_group(0x53530001)
    assert len(calls) == 1
    kind, payload, _ = calls[0]
    assert kind == 17
    assert struct.unpack_from("=i", payload, 4) == (0,)
    assert _attributes(payload[16:]) == {27: struct.pack("=I", 0x53530001)}
    for group in (0, 1, True, 0x100000000):
        with pytest.raises(ValueError):
            channel.delete_group(group)
    assert len(calls) == 1


def test_wrong_sequence_ack_fails_without_being_accepted():
    channel = _RouteSocket.__new__(_RouteSocket)
    channel._sequence = 6
    class Wire:
        def sendto(self, payload, address): pass
        def recvmsg(self, size):
            return struct.pack("=IHHIIi", 20, 2, 0, 6, 0, 0), [], 0, (0, 0)
    channel._socket = Wire()
    with pytest.raises(RuntimeError, match="framing"):
        channel.delete_group(0x53530001)
    assert channel._sequence == 7


@pytest.mark.parametrize("name,group,reject", [("foreign", 0, False), ("sv2foreign", 0, True), ("foreign", 0x53530001, True), ("foreign", 0x53530010, False)])
def test_initial_inventory_preserves_unrelated_links_and_rejects_dirty_owned_range(name, group, reject):
    from skyseed_v2.backend.bpf._runner.kernel_netlink import _ifinfo, _text, _u32
    channel, _ = _capture()
    channel.request = lambda *args, **kwargs: [
        (16, _ifinfo(1) + _text(3, "lo")),
        (16, _ifinfo(2) + _text(3, name) + _u32(27, group)),
    ]
    if reject:
        with pytest.raises(RuntimeError, match="already exists"):
            channel.initial_links(0x53530005)
    else:
        assert channel.initial_links(0x53530005) == 1


def test_netlink_call_counters_are_local_to_each_worker():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from skyseed_v2.backend.bpf._runner import kernel_netlink
    before = kernel_netlink._request_count()
    ready = Barrier(2)
    def call(count):
        channel = _RouteSocket.__new__(_RouteSocket)
        channel._sequence = 0
        class Wire:
            def sendto(self, payload, address): self.sequence = struct.unpack_from("=I", payload, 8)[0]
            def recvmsg(self, size): return struct.pack("=IHHIIi", 20, 2, 0, self.sequence, 0, 0), [], 0, (0, 0)
        channel._socket = Wire()
        first = kernel_netlink._request_count()
        ready.wait()
        for _ in range(count): channel.delete_group(0x53530001)
        return kernel_netlink._request_count() - first
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(call, (7, 11))) == [7, 11]
    assert kernel_netlink._request_count() == before


def _dump_channel(messages):
    channel = _RouteSocket.__new__(_RouteSocket); channel._sequence = 0
    class Wire:
        def sendto(self, payload, address): self.sent = payload
        def recvmsg(self, size): return messages.pop(0), [], 0, (0, 0)
    channel._socket = Wire()
    return channel


def _message(kind, body, *, flags=2):
    return struct.pack('=IHHII', 16+len(body), kind, flags, 1, 0)+body


def test_ipv4_dump_waits_past_ack_and_retains_local_point_to_point_addresses():
    from ipaddress import IPv4Address
    body = struct.pack('=BBBBI', socket.AF_INET, 24, 0, 0, 2)
    body += _attr(1, IPv4Address('192.0.2.99').packed)+_attr(2, IPv4Address('192.0.2.1').packed)
    channel = _dump_channel([_message(2, struct.pack('=i',0)), _message(20, body),
                             _message(20, body), _message(3, struct.pack('=i',0))])
    assert channel.ipv4_address_facts() == ((IPv4Address('192.0.2.1'),24),)*2
    header=struct.unpack_from('=IHHII',channel._socket.sent)
    assert header[1:3] == (22, 0x305)


@pytest.mark.parametrize('kind,body,flags,error', [
    (3,struct.pack('=i',-errno.ENOBUFS),2,OSError),
    (2,struct.pack('=i',-errno.EPERM),0,OSError),
    (3,b'',2,RuntimeError),
    (20,b'',0x12,RuntimeError),
    (3,struct.pack('=i',0),0x12,RuntimeError),
    (4,b'',0,RuntimeError),
])
def test_ipv4_dump_rejects_incomplete_or_failed_kernel_results(kind,body,flags,error):
    channel=_dump_channel([_message(kind,body,flags=flags)])
    with pytest.raises(error): channel.ipv4_address_facts()


@pytest.mark.parametrize('packet,flags,sender,message', [
    (b'', 0, (0, 0), 'before completion'),
    (b'partial', 0, (0, 0), 'header'),
    (_message(3, struct.pack('=i', 0)), socket.MSG_TRUNC, (0, 0), 'truncated'),
    (_message(3, struct.pack('=i', 0)), 0, (42, 0), 'not from the kernel'),
])
def test_ipv4_dump_rejects_invalid_datagram(packet, flags, sender, message):
    channel = _dump_channel([])
    channel._socket.recvmsg = lambda size: (packet, [], flags, sender)
    with pytest.raises(RuntimeError, match=message):
        channel.ipv4_address_facts()


def test_ipv4_routes_include_all_tables_host_local_and_nondefault_prefixes():
    from ipaddress import IPv4Address,IPv4Network
    calls=[];channel=_RouteSocket.__new__(_RouteSocket)
    def route(prefix,table,destination=None):
        body=struct.pack('=BBBBBBBBI',socket.AF_INET,prefix,0,0,table,2,254,2,0)
        return (24,body+(_attr(1,IPv4Address(destination).packed) if destination else b''))
    def request(kind,payload,**kwargs):
        calls.append((kind,payload,kwargs))
        return [route(0,254),route(32,255,'127.0.0.1'),route(24,0,'192.0.2.0'),route(16,100,'10.1.0.0')]
    channel.request=request
    assert channel.ipv4_route_networks()==tuple(map(IPv4Network,['127.0.0.1/32','192.0.2.0/24','10.1.0.0/16']))
    kind,payload,kwargs=calls[0]
    assert kind==26 and payload[4]==0 and kwargs['flags']==0x300


@pytest.mark.parametrize('body', [b'', struct.pack('=BBBBI',socket.AF_INET,33,0,0,1),
    struct.pack('=BBBBI',socket.AF_INET,24,0,0,1)+b'\x08\x00',
    struct.pack('=BBBBI',socket.AF_INET,24,0,0,1)+_attr(2,b'\x01')])
def test_ipv4_inventory_rejects_malformed_records(body):
    channel=_RouteSocket.__new__(_RouteSocket);channel.request=lambda *a,**kw:[(20,body)]
    with pytest.raises(RuntimeError):channel.ipv4_address_facts()


@pytest.mark.parametrize('collision_source', [None,'address','route'])
def test_initial_collision_audit_uses_retained_transport_without_commands(monkeypatch,collision_source):
    from ipaddress import IPv4Address,IPv4Network
    from skyseed_v2.backend.bpf._runner import privileged_runtime_command as command
    calls=[]
    class Netlink:
        def ipv4_address_facts(self):
            calls.append('address');return ((IPv4Address('100.64.0.2' if collision_source=='address' else '192.0.2.1'),24),)
        def ipv4_route_networks(self):
            calls.append('route');return (IPv4Network('100.64.0.0/24' if collision_source=='route' else '192.0.2.0/24'),)
    monkeypatch.setattr(command,'_LIVE_NODES',{'a':{'netlink':Netlink()}})
    monkeypatch.setattr(command,'_assert_netns',lambda node:calls.append('identity'))
    monkeypatch.setattr(command,'_in_netns',lambda *a:pytest.fail('initial inventory must not launch a command'))
    request={'node_address_range':'100.64.0.0/29','zone_prefixes':(), 'nodes':[{'key':'a','node_address':'100.64.0.1'}], 'worker_count':2}
    if collision_source:
        with pytest.raises(RuntimeError,match='namespace '+collision_source):command._audit_node_addresses(request)
    else:
        result=command._audit_node_addresses(request)
        assert result['observed_ipv4_address_prefixes']==result['observed_non_default_ipv4_route_prefixes']==1
    assert calls==['identity','address','route']
