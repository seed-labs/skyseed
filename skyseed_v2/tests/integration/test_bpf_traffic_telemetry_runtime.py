from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("continued", (False, True))
def test_privileged_kernel_accepts_instrumented_sched_cls_program(continued) -> None:
    image = os.environ.get("SKYSEED_BPF_HELPER_IMAGE")
    if not image:
        pytest.skip("set SKYSEED_BPF_HELPER_IMAGE to run the privileged verifier test")
    script = r"""
import json
import os
from skyseed_v2.backend.bpf.runner.bpf import (
    _BPF_MAP_TYPE_ARRAY,
    _BPF_MAP_TYPE_RINGBUF,
    _create_map,
    _load_program,
)
descriptors = []
try:
    active_generation = _create_map("t_active_gen", key_size=4, max_entries=1, map_type=_BPF_MAP_TYPE_ARRAY)
    active_links = _create_map("t_links", key_size=8, max_entries=8)
    routes = _create_map("t_routes", key_size=8, max_entries=8)
    ingress = _create_map("t_ingress", key_size=4, max_entries=8)
    egress = _create_map("t_egress", key_size=4, max_entries=8)
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
    versions = _create_map("t_versions", key_size=4, max_entries=8)
    readers = _create_map("t_readers", key_size=4, value_size=8, max_entries=8)
    descriptors.extend((active_generation, active_links, routes, ingress, egress, events, losses, versions, readers))
    program = _load_program(
        active_generation,
        active_links,
        routes,
        ingress,
        egress,
        telemetry_ring_map=events,
        telemetry_loss_map=losses,
        node_slot=0,
        generation_versions_map=versions,
        generation_readers_map=readers if CONTINUED else None,
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
        script.replace("CONTINUED", repr(continued)),
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
