import struct
import sys
from types import SimpleNamespace

from skyseed.pc.compiler.bpf import encode_rule
from skyseed.runner.bpf import versions


def finish(work):
    while True:
        try:
            next(work)
        except StopIteration as result:
            return result.value


class Maps:
    def __init__(self):
        self.values = {}
        self.next_fd = 10

    def create(self, *args, **kwargs):
        self.next_fd += 1
        self.values[self.next_fd] = {}
        return self.next_fd

    def update(self, fd, key, value):
        self.values[fd][key] = value

    def delete(self, fd, key):
        self.values[fd].pop(key, None)

    def write(self, fd, entries, **kwargs):
        self.values[fd].update(entries)
        return True

    def lookup(self, fd, key, size):
        return self.values[fd].get(key)

    def object(self, content, shared):
        maps = {name: self.create() for name in ("rule_versions", "node_versions", "port_bindings")}
        maps.update(shared or {name: self.create() for name in ("active_generation", "generation_readers")})
        return SimpleNamespace(maps=maps, close=lambda: None)


def rule(node, label):
    key, value = encode_rule(("prefix", "192.0.2.0/24"), "push", 1, label)
    return {"node_id": node, "parameters": {"key_hex_le": key.hex(), "value_hex_le": value.hex(),
            "action": "push", "kind": "install", "prefix": "192.0.2.0/24"}}


def test_retirement_waits_for_readers_and_unchanged_nodes_reuse_versions(monkeypatch):
    maps = Maps()
    monkeypatch.setattr(versions.kernel, "create_map", maps.create)
    monkeypatch.setattr(versions.kernel, "update", maps.update)
    monkeypatch.setattr(versions.kernel, "delete", maps.delete)
    monkeypatch.setattr(versions.kernel, "write_entries", maps.write)
    monkeypatch.setattr(versions.kernel, "lookup", maps.lookup)
    monkeypatch.setattr(versions.kernel, "BpfObject", maps.object)
    closed = []
    monkeypatch.setattr(versions, "os", SimpleNamespace(sys=sys, close=closed.append))
    owner = versions.LabelVersions(b"test")
    owner.add_node("a")
    owner.add_node("b")
    first = finish(owner.prepare([rule("a", 20), rule("b", 30)]))
    owner.publish(first)
    a_old, b_old = owner.selected["a"], owner.selected["b"]
    a_fd = owner.tables["a", a_old]
    a_new = finish(owner.prepare_node("a", [rule("a", 40)]))
    second = finish(owner.generation({"a": a_new}))
    assert owner.selected == {"a": a_old, "b": b_old}
    owner.publish(second)
    assert owner.selected == {"a": a_new, "b": b_old}
    maps.update(owner.shared["generation_readers"], first, struct.pack("=Q", 1))
    retirement = owner.reclaim(first)
    next(retirement)
    assert a_fd not in closed
    maps.update(owner.shared["generation_readers"], first, bytes(8))
    finish(retirement)
    assert a_fd in closed
    assert ("b", b_old) in owner.tables
    # More updates than the kernel generation maps' 128 slots: reclamation
    # keeps retained material proportional to live generations, not run length.
    for label in range(100, 240):
        version = finish(owner.prepare_node("a", [rule("a", label)]))
        generation = finish(owner.generation({"a": version}))
        previous = owner.publish(generation)
        finish(owner.reclaim(previous))
        assert len(owner.generations) == 1
        assert len(owner.tables) == 2
    remaining = set(owner.tables.values())
    owner.close()
    assert remaining <= set(closed)
