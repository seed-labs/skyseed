"""Prepared node tables, shared publication and reader-aware reclamation."""
from collections import Counter
import os
import struct

from . import kernel


class LabelVersions:
    def __init__(self, content):
        self.content = content
        self.objects = {}
        self.shared = {}
        self.rules = {}
        self.local_rules = {}
        self.selected = {}
        self.tables = {}
        self.table_local = {}
        self.pending = set()
        self.references = Counter()
        self.generations = {}
        self.local_generations = {}
        self.active = 0
        self.next_version = 1
        self.next_generation = 1
        self.batch_supported = True

    def add_node(self, node):
        if node in self.objects:
            return
        obj = kernel.BpfObject(self.content, self.shared)
        self.objects[node] = obj
        if not self.shared:
            self.shared = {name: obj.maps[name] for name in ("active_generation", "generation_readers")}
        self.rules[node] = {}
        self.local_rules[node] = {}
        self.selected[node] = 0

    def bind(self, node, port, index, source_mac, peer_mac):
        value = struct.pack("=I", index)
        value += bytes.fromhex(source_mac.replace(":", ""))
        value += bytes.fromhex(peer_mac.replace(":", ""))
        kernel.update(self.objects[node].maps["port_bindings"], port, value)

    def unbind(self, node, port):
        kernel.delete(self.objects[node].maps["port_bindings"], port)

    def prepare_node(self, node, instructions, *, unit_size=64):
        """Prepare one node independently, preserving that node's update order."""
        rules, local_rules = self.rules[node], self.local_rules[node]
        for instruction in instructions:
            parameters = instruction["parameters"]
            key = bytes.fromhex(parameters["key_hex_le"])
            value = bytes.fromhex(parameters["value_hex_le"])
            # Compiler's portable table bytes are little endian for integer fields.
            if os.sys.byteorder != "little":
                key = struct.pack("=I", struct.unpack("<I", key[:4])[0]) + key[4:]
                value = struct.pack("=III", *struct.unpack("<III", value))
            if parameters["kind"] == "withdraw":
                rules.pop(key, None)
                local_rules.pop(key, None)
            else:
                rules[key] = value
                if parameters["action"] in ("deliver", "pop_deliver"):
                    local_rules[key] = parameters["prefix"]
                else:
                    local_rules.pop(key, None)
        version = self.next_version
        self.next_version += 1
        fd = kernel.create_map("ss_rules", 11, 12, 12, 1048576, flags=1)
        key = (node, version)
        self.tables[key] = fd
        self.table_local[key] = set(local_rules.values())
        self.references[key] = 1  # Prepared owner before any generation refers to it.
        self.pending.add(key)
        entries = list(rules.items())
        for offset in range(0, len(entries), unit_size):
            self.batch_supported = kernel.write_entries(fd, entries[offset:offset + unit_size],
                                                        batch=self.batch_supported)
            yield
        kernel.update(self.objects[node].maps["rule_versions"], version, fd)
        return version

    def generation(self, updates, *, unit_size=64):
        """One publication selects ready changes, retaining all other active nodes."""
        generation = self.next_generation
        self.next_generation += 1
        selected = dict(self.selected)
        selected.update(updates)
        self.generations[generation] = selected
        self.local_generations[generation] = {
            node: self.table_local[node, version] for node, version in selected.items()
            if version and self.table_local[node, version]}
        self.references.update((node, version) for node, version in selected.items() if version)
        entries = list(selected.items())
        for offset in range(0, len(entries), unit_size):
            for node, version in entries[offset:offset + unit_size]:
                kernel.update(self.objects[node].maps["node_versions"], generation, version)
            yield
        kernel.update(self.shared["generation_readers"], generation, bytes(8))
        return generation

    def prepare(self, instructions):
        """Initial-state convenience; scheduled work uses independent node jobs."""
        nodes = {}
        for instruction in instructions:
            nodes.setdefault(instruction["node_id"], []).append(instruction)
        updates = {}
        for node, operations in nodes.items():
            updates[node] = yield from self.prepare_node(node, operations)
        return (yield from self.generation(updates))

    def publish(self, generation):
        previous = self.active
        kernel.update(self.shared["active_generation"], 0, generation)
        self.active = generation
        self.selected = dict(self.generations[generation])
        for node, version in self.selected.items():
            key = (node, version)
            if key in self.pending:
                self.pending.remove(key)
                self.references[key] -= 1
        return previous

    def reclaim(self, generation, *, unit_size=64):
        if generation == self.active:
            raise ValueError("cannot reclaim the active generation")
        value = kernel.lookup(self.shared["generation_readers"], generation, 8)
        while value is not None and int.from_bytes(value, os.sys.byteorder):
            yield
            value = kernel.lookup(self.shared["generation_readers"], generation, 8)
        selected = self.generations.pop(generation)
        self.local_generations.pop(generation)
        entries = list(selected.items())
        for offset in range(0, len(entries), unit_size):
            for node, version in entries[offset:offset + unit_size]:
                kernel.delete(self.objects[node].maps["node_versions"], generation)
                if not version:
                    continue
                key = (node, version)
                self.references[key] -= 1
                if not self.references[key]:
                    kernel.delete(self.objects[node].maps["rule_versions"], version)
                    os.close(self.tables.pop(key))
                    del self.table_local[key]
                    del self.references[key]
            yield
        kernel.delete(self.shared["generation_readers"], generation)

    def close(self):
        for fd in self.tables.values():
            os.close(fd)
        self.tables.clear()
        for obj in self.objects.values():
            obj.close()
        self.objects.clear()
