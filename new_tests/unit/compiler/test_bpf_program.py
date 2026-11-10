import ctypes
import os
from pathlib import Path
import shutil
import struct
import subprocess

import pytest

from skyseed.pc.compiler.bpf import encode_rule


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "new_src/skyseed/pc/compiler/bpf"


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    clang = os.environ.get("SKYSEED_TEST_CLANG") or shutil.which("clang")
    if clang is None:
        pytest.skip("offline BPF C validation requires clang with the BPF target")
    output = tmp_path_factory.mktemp("label-program")
    for target in ("bpfel", "bpfeb"):
        result = subprocess.run([clang, "-target", target, "-O2", "-g", "-Wall", "-Werror",
                                 "-I/usr/include/x86_64-linux-gnu", "-c",
                                 str(SOURCE / "label_forwarding.bpf.c"), "-o", str(output / f"{target}.o")],
                                text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        elf = (output / f"{target}.o").read_bytes()
        assert elf[:4] == b"\x7fELF"
        endian = "<" if target == "bpfel" else ">"
        assert struct.unpack_from(endian + "H", elf, 18)[0] == 247  # EM_BPF
    library = output / "label_test.so"
    result = subprocess.run([clang, "-shared", "-fPIC", "-nostdlib", "-O2", "-I", str(SOURCE),
                             str(Path(__file__).with_name("bpf_packet_harness.c")), "-o", str(library)],
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    loaded = ctypes.CDLL(str(library))
    loaded.run_packet.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
                                  ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int]
    loaded.run_packet.restype = ctypes.c_int
    loaded.prepare_generations.argtypes = [ctypes.c_void_p] * 4
    loaded.prepare_generations.restype = None
    loaded.publish_generation.argtypes = [ctypes.c_int]
    loaded.publish_generation.restype = None
    loaded.remove_node_version.argtypes = [ctypes.c_int, ctypes.c_int]
    loaded.remove_node_version.restype = None
    loaded.run_generation_packet.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int),
                                             ctypes.c_int, ctypes.c_int]
    loaded.run_generation_packet.restype = ctypes.c_int
    loaded.remaining_readers.restype = ctypes.c_ulonglong
    return loaded


@pytest.fixture
def program(library):

    def run(packet, match, action, port=1, label=0, bound=True):
        key, value = encode_rule(match, action, port, label)
        buffer = ctypes.create_string_buffer(packet, len(packet) + 8)
        length = ctypes.c_int(len(packet))
        result = library.run_packet(buffer, ctypes.byref(length), key, value, bound)
        return result, buffer.raw[:length.value]

    return run


def ipv4(destination=b"\xc0\x00\x02\x01"):
    ip = bytes.fromhex("450000200000000040110000c6336401") + destination + b"data-payload"
    return b"\x02\x00\x00\x00\x00\x01" * 2 + b"\x08\x00" + ip


def labelled(label, *, ttl=64, packet=None):
    packet = ipv4() if packet is None else packet
    return packet[:12] + b"\x88\x47" + struct.pack("!I", label << 12 | 0x100 | ttl) + packet[14:]


def test_real_program_push_swap_and_pop_keep_payload_and_use_local_labels(program):
    original = ipv4()
    result, pushed = program(original, ("prefix", "192.0.2.0/24"), "push", label=20)
    assert result == 7
    assert pushed[12:18] == bytes.fromhex("884700014140")
    assert pushed[18:] == original[14:]
    result, swapped = program(pushed, ("label", 20), "swap", label=30)
    assert result == 7 and swapped[14:18] == bytes.fromhex("0001e13f")
    assert swapped[18:] == original[14:]
    result, popped = program(swapped, ("label", 30), "pop_deliver")
    assert result == 0 and popped[12:] == original[12:]


def test_transit_ignores_business_ip_but_drops_unbound_ports_wrong_labels_and_expired_ttl(program):
    for destination in (b"\xc0\x00\x02\x01", b"\xcb\x00\x71\x09"):
        packet = labelled(20, packet=ipv4(destination))
        assert program(packet, ("label", 20), "swap", label=30)[0] == 7
        assert program(packet, ("label", 20), "swap", label=30, bound=False)[0] == 2
        assert program(packet, ("label", 21), "swap", label=30)[0] == 2
    assert program(labelled(20, ttl=1), ("label", 20), "swap", label=30)[0] == 2
    assert program(b"short", ("label", 20), "swap", label=30)[0] == 2


@pytest.fixture
def generations(library):
    key, old = encode_rule(("prefix", "192.0.2.0/24"), "push", 1, 30)
    _, new = encode_rule(("prefix", "192.0.2.0/24"), "push", 1, 40)
    _, unchanged = encode_rule(("prefix", "192.0.2.0/24"), "push", 1, 60)
    library.prepare_generations(key, old, new, unchanged)

    def run(node=0, race=0, packet=None):
        packet = ipv4() if packet is None else packet
        buffer = ctypes.create_string_buffer(packet, len(packet) + 8)
        length = ctypes.c_int(len(packet))
        result = library.run_generation_packet(buffer, ctypes.byref(length), node, race)
        assert library.remaining_readers() == 0
        if result == 7:
            assert library.read_was_protected() == 1
            label = struct.unpack_from("!I", buffer, 14)[0] >> 12
            return label, library.last_version()
        return result, library.last_version()

    return library, run


def test_one_publication_selects_new_node_version_and_reuses_unchanged_node(generations):
    library, run = generations
    assert run(0) == (30, 1)
    assert run(1) == (60, 1)
    library.publish_generation(2)
    assert run(0) == (40, 2)
    assert run(1) == (60, 1)


@pytest.mark.parametrize("race,expected", [(1, (40, 2)), (2, (30, 1)), (3, (30, 1))])
def test_publication_races_pin_one_version_and_keep_copied_push_decision(generations, race, expected):
    library, run = generations
    # Before pin: retry new generation. After pin / before TC continuation:
    # finish using the old decision, then the next invocation uses the new one.
    assert run(race=race) == expected
    assert run() == (40, 2)


def test_missing_generation_table_and_rule_release_readers_and_drop(generations):
    library, run = generations
    assert run(packet=ipv4(b"\xcb\x00\x71\x09"))[0] == 2
    library.remove_node_version(0, 1)
    assert run()[0] == 2
    library.publish_generation(0)
    assert run()[0] == 2
