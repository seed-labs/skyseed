from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_privileged_kernel_accepts_instrumented_sched_cls_program() -> None:
    image = os.environ.get("SKYSEED_BPF_HELPER_IMAGE")
    if not image:
        pytest.skip("set SKYSEED_BPF_HELPER_IMAGE to run the privileged verifier test")
    script = r"""
import json
import os
from skyseed_v2.backend.bpf._runner.privileged_runtime_command import (
    _BPF_MAP_TYPE_ARRAY,
    _BPF_MAP_TYPE_RINGBUF,
    _create_map,
    _load_program,
)
descriptors = []
try:
    active_generation = _create_map("t_active_gen", key_size=4, max_entries=1)
    active_links = _create_map("t_links", key_size=8, max_entries=8)
    routes = _create_map("t_routes", key_size=8, max_entries=8)
    events = _create_map(
        "test_events",
        key_size=0,
        value_size=0,
        max_entries=4096,
        map_type=_BPF_MAP_TYPE_RINGBUF,
    )
    losses = _create_map(
        "test_losses",
        key_size=4,
        value_size=8,
        max_entries=1,
        map_type=_BPF_MAP_TYPE_ARRAY,
    )
    descriptors.extend((active_generation, active_links, routes, events, losses))
    program = _load_program(
        active_generation,
        active_links,
        routes,
        telemetry_ring_map=events,
        telemetry_loss_map=losses,
        node_slot=0,
    )
    descriptors.append(program)
    print(json.dumps({"status": "ok", "program_fd": program}))
finally:
    for descriptor in reversed(descriptors):
        os.close(descriptor)
"""
    command = (
        "docker",
        "run",
        "--rm",
        "--privileged",
        "--pid",
        "host",
        "--network",
        "host",
        "--mount",
        "type=bind,source=/,target=/host,readonly",
        "--entrypoint",
        "/usr/sbin/chroot",
        image,
        "/host",
        sys.executable,
        "-c",
        script,
    )
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "ok"


def test_privileged_kernel_accepts_exact_hash_map_batch_delete() -> None:
    image = os.environ.get("SKYSEED_BPF_HELPER_IMAGE")
    if not image:
        pytest.skip("set SKYSEED_BPF_HELPER_IMAGE to run the privileged batch test")
    script = r"""
import json
import os
from skyseed_v2.backend.bpf._runner.privileged_runtime_command import (
    _create_map,
    _delete_map_batch,
    _lookup_map,
    _update_map,
)
descriptor = _create_map("t_batch_del", key_size=8, max_entries=8)
try:
    keys = ((7, 11), (7, 13), (7, 17), (7, 19))
    for index, key in enumerate(keys, start=1):
        _update_map(descriptor, key, index)
    deleted = _delete_map_batch(descriptor, keys)
    remaining = [_lookup_map(descriptor, key) for key in keys]
    print(json.dumps({"deleted": deleted, "remaining": remaining}))
finally:
    os.close(descriptor)
"""
    command = (
        "docker",
        "run",
        "--rm",
        "--privileged",
        "--pid",
        "host",
        "--network",
        "host",
        "--mount",
        "type=bind,source=/,target=/host,readonly",
        "--entrypoint",
        "/usr/sbin/chroot",
        image,
        "/host",
        sys.executable,
        "-c",
        script,
    )
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "deleted": 4,
        "remaining": [None, None, None, None],
    }
