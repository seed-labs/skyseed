"""Execute actual emitted instructions without loading BPF into the kernel."""
from __future__ import annotations

import struct

import pytest

from skyseed_v2.backend.bpf._runner import privileged_runtime_command as command


def _execute_program(maps, *, ingress=11, destination="198.18.0.1"):
    code = command._tc_program(10, 11, 12, 13, 14)
    memory = {}
    def write(address, value, size):
        for i, byte in enumerate(int(value).to_bytes(size, "little", signed=False)):
            memory[address + i] = byte
    def read(address, size):
        return int.from_bytes(bytes(memory[address + i] for i in range(size)), "little")
    registers = [0] * 11
    registers[1], registers[10] = 1000, 10000
    write(1076, 2000, 4); write(1080, 2034, 4); write(1040, ingress, 4)
    write(2012, 0x0008, 2)
    write(2030, command._ip_key(destination), 4)
    pc = 0
    lookups = []
    while pc < len(code) // 8:
        opcode, regs, offset, immediate = struct.unpack_from("<BBhi", code, pc * 8)
        destination_register, source = regs & 15, regs >> 4
        pc += 1
        if opcode == 0x18:  # LDDW map descriptor
            high = struct.unpack_from("<i", code, pc * 8 + 4)[0]
            registers[destination_register] = (high << 32) | (immediate & 0xFFFFFFFF)
            pc += 1
        elif opcode in (0x61, 0x69):
            registers[destination_register] = read(registers[source] + offset, 4 if opcode == 0x61 else 2)
        elif opcode == 0x63:
            write(registers[destination_register] + offset, registers[source] & 0xFFFFFFFF, 4)
        elif opcode == 0xBF:
            registers[destination_register] = registers[source]
        elif opcode == 0xB7:
            registers[destination_register] = immediate & 0xFFFFFFFFFFFFFFFF
        elif opcode == 0x07:
            registers[destination_register] += immediate
        elif opcode == 0x85:
            if immediate == command._BPF_FUNC_MAP_LOOKUP_ELEM:
                fd, address = registers[1:3]
                key = ((read(address, 4), read(address + 4, 4)) if fd in (11, 12) else read(address, 4))
                lookups.append((fd, key))
                value = maps[fd].get(key)
                registers[0] = 0 if value is None else 30000 + len(lookups) * 8
                if value is not None:
                    write(registers[0], value, 4)
                for index in range(1, 6): registers[index] = 0
            elif immediate == command._BPF_FUNC_REDIRECT:
                return ("redirect", registers[1]), lookups
            else:
                raise AssertionError(f"unhandled helper {immediate}")
        elif opcode == 0x95:
            return ("action", registers[0]), lookups
        elif opcode in (0x05, 0x15, 0x55, 0x2D):
            jump = (opcode == 0x05 or opcode == 0x15 and registers[destination_register] == immediate
                    or opcode == 0x55 and registers[destination_register] != immediate
                    or opcode == 0x2D and registers[destination_register] > registers[source])
            if jump: pc += offset
        else:
            raise AssertionError(f"unhandled emitted instruction {opcode:#x}")
    raise AssertionError("program did not terminate")


def _maps():
    destination = command._ip_key("198.18.0.1")
    return {10: {0: 0}, 11: {(0, 3): 1, (1, 3): 1},
            12: {(0, destination): 1, (1, destination): 2},
            13: {11: 3}, 14: {1: 21, 2: 22}}


def test_future_entries_have_no_visibility_until_active_generation_commit():
    maps = _maps()
    assert _execute_program(maps)[0] == ("redirect", 21)
    maps[10][0] = 1
    assert _execute_program(maps)[0] == ("redirect", 22)


@pytest.mark.parametrize("missing", [10, 11, 12, 13, 14])
def test_required_generation_link_route_or_live_binding_absence_drops(missing):
    maps = _maps()
    maps[missing].clear()
    assert _execute_program(maps)[0] == ("action", command._TC_ACT_SHOT)


def test_logical_local_route_zero_preserves_pass_without_egress_binding():
    maps = _maps()
    maps[12][(0, command._ip_key("198.18.0.1"))] = 0
    maps[14].clear()
    result, lookups = _execute_program(maps)
    assert result == ("action", command._TC_ACT_OK)
    assert all(fd != 14 for fd, key in lookups)


@pytest.mark.parametrize("binding_map,key", [(13, 11), (14, 1)])
def test_zero_live_binding_fails_closed(binding_map, key):
    maps = _maps()
    maps[binding_map][key] = 0
    assert _execute_program(maps)[0] == ("action", command._TC_ACT_SHOT)
