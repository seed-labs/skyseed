"""Execute default and custom C with native helper substitutes, without BPF load."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from skyseed_v2.backend.bpf.dataplane.program import _INCLUDE, _DEFAULT, _compiler

_FIXTURES = Path(__file__).parents[1] / 'fixtures/dataplane'


@pytest.fixture(scope='module', params=['default', 'custom'])
def native_program(request, tmp_path_factory):
    root = tmp_path_factory.mktemp('native-c-' + request.param)
    (root / 'bpf').mkdir()
    shutil.copy(_FIXTURES / 'bpf_helpers.h', root / 'bpf/bpf_helpers.h')
    # Only change storage qualifiers so the host harness can bind configuration.
    # The BPF loader binds this same configuration through libbpf's initial value.
    (root / 'skyseed.h').write_text((_INCLUDE / 'skyseed.h').read_text().replace(
        'const volatile struct sky_configuration sky_config', 'volatile struct sky_configuration sky_config'))
    if request.param == 'default':
        shutil.copy(_DEFAULT, root / 'selected.c')
    else:
        (root / 'selected.c').write_text('''
#include "skyseed.h"
SKY_MAP(private_packets, BPF_MAP_TYPE_ARRAY, __u32, __u64);
static int handle(struct sky_context *ctx) {
    __u32 zero = 0;
    __u64 *count = bpf_map_lookup_elem(&private_packets, &zero);
    if (count) __sync_fetch_and_add(count, 1);
    /* Mutation cannot change the ingress packet facts already captured. */
    unsigned char *data = (void *)(long)ctx->skb->data;
    data[26] = 99;
    return TC_ACT_OK;
}
SKYSEED_PROGRAM(handle);
''')
    output = root / 'execute'
    subprocess.run([_compiler(), '-O2', '-no-pie',
                    *([] if shutil.which('ld') else [
                        '--ld-path=' + str(Path(sys.prefix) / 'bin/x86_64-conda-linux-gnu-ld'),
                        '-nostdlib', '/usr/lib/x86_64-linux-gnu/crt1.o', '/usr/lib/x86_64-linux-gnu/crti.o',
                        '/lib/x86_64-linux-gnu/libc.so.6', '/usr/lib/x86_64-linux-gnu/crtn.o',
                        '-Wl,--dynamic-linker=/lib64/ld-linux-x86-64.so.2']), '-I', str(root), '-I', str(Path(sys.prefix) / 'include'),
                    *(['-DDEFAULT_TEST'] if request.param == 'default' else []),
                    str(_FIXTURES / 'native.c'), '-o', str(output)], check=True)
    def run(mode=0, telemetry=True, continuation=True, *, local=False, route=None):
        return json.loads(subprocess.check_output([str(output), str(mode), str(int(telemetry)), str(int(continuation)), str(int(local)), *([] if route is None else [str(route)])]))
    return request.param, run


@pytest.mark.parametrize('telemetry', [False, True])
@pytest.mark.parametrize('continuation', [False, True])
@pytest.mark.parametrize('mode', [0, 1, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20, 21, 30, 31, 32, 33, 40])
def test_handler_returns_complete_observation_and_release(native_program, mode, telemetry, continuation):
    kind, run = native_program
    result = run(mode, telemetry, continuation)
    assert result['readers'] == 0
    assert result['events'] == int(telemetry)
    assert result['losses'] == int(telemetry and mode == 40)
    if kind == 'custom':
        assert result['action'] == 0 and result['private'] == 1
        assert result['reason'] == 0
    else:
        expected = 0 if mode in (16, 30) else 2 if mode in (10, 11, 12, 13, 14, 15, 17, 18, 31, 32) or continuation and mode in (20, 21) else 7
        assert result['action'] == expected
        if expected == 7:
            assert result['output'] == (22 if mode == 1 else 21)
    if telemetry:
        assert result['icmp'] == int(mode not in (30, 31, 32, 33))
        assert result['facts'] == int(mode not in (30, 31, 32))
        if result['facts']:
            assert result['source'] == 0x01000064
        if continuation and mode in (20, 21) or mode == 10:
            assert result['generation'] == 0xffffffff
