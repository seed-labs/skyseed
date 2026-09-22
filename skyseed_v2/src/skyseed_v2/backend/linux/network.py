"""Owned namespace, identity and connection primitives shared by both adapters."""
import ctypes
import errno
import os
from .netlink import _RouteSocket
from .materials import _IDENTITY_INTERFACE
from skyseed_v2.backend.runner.process_incarnation import _read_process_start_time_ticks


def _setns(fd):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.setns(ctypes.c_int(fd), ctypes.c_int(0x40000000)) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _assert_netns(node):
    pid = int(node["pid"])
    if _read_process_start_time_ticks(pid) != int(node["process_start_time_ticks"]):
        raise RuntimeError(f"node process incarnation is stale: pid={pid}")
    if os.stat(f"/proc/{pid}/ns/net").st_ino != int(node["netns_inode"]):
        raise RuntimeError(f"node network namespace incarnation is stale: pid={pid}")


def _retain_namespaces(nodes, live_nodes, *, assert_netns=_assert_netns, route_socket=_RouteSocket, setns=_setns):
    if live_nodes:
        raise RuntimeError("namespace foundation is already retained")
    original = os.open("/proc/self/ns/net", os.O_RDONLY | os.O_CLOEXEC)
    try:
        for node in nodes:
            assert_netns(node)
            fd = os.open(f"/proc/{int(node['pid'])}/ns/net", os.O_RDONLY | os.O_CLOEXEC)
            try:
                if os.fstat(fd).st_ino != int(node["netns_inode"]):
                    raise RuntimeError("namespace changed during Initialization")
                setns(fd)
                channel = route_socket()
                live_nodes[node["key"]] = {**node, "namespace_fd": fd, "netlink": channel,
                                           "indexes": {}, "maps": {}}
            except BaseException:
                os.close(fd)
                raise
            finally:
                setns(original)
    finally:
        os.close(original)


def _create_identity(node, address):
    channel = node["netlink"]
    node["identity_owned"] = True
    try:
        channel.create_dummy(_IDENTITY_INTERFACE)
    except OSError as error:
        if error.errno == errno.EEXIST:
            node["identity_owned"] = False
        raise
    node["identity"] = channel.link_index(_IDENTITY_INTERFACE)
    channel.address(node["identity"], address, exclusive=True)
    channel.set_link(node["identity"], up=True)


def _delete_identity(node):
    if not node.get("identity_owned"):
        return
    try:
        index = node.get("identity")
        if index is None:
            index = node["netlink"].link_index(_IDENTITY_INTERFACE)
        node["netlink"].delete_link(index)
    except OSError as error:
        if error.errno not in (errno.ENODEV, errno.ENOENT):
            raise
    node["identity_owned"] = False
    node.pop("identity", None)


def _create_connection(nodes, connection, *, mac=None, group=0, peer_group=0):
    node, peer = nodes[connection["node_a"]], nodes[connection["node_b"]]
    node["netlink"].create_veth(connection["interface_a"], connection["interface_b"],
                               peer["namespace_fd"], mac=mac, group=group, peer_group=peer_group)


def _delete_connection(nodes, connection):
    node = nodes[connection["node_a"]]
    name = connection["interface_a"]
    try:
        index = node["indexes"].get(name)
        if index is None:
            index = node["netlink"].link_index(name)
        node["netlink"].delete_link(index)
    except OSError as error:
        if error.errno not in (errno.ENODEV, errno.ENOENT):
            raise
    for side in ("a", "b"):
        nodes[connection[f"node_{side}"]]["indexes"].pop(connection[f"interface_{side}"], None)
