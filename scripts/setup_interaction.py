#!/usr/bin/env python3
"""Install a pinned etcd executable in a user-owned directory, never a daemon."""
import argparse
import hashlib
import io
from pathlib import Path
import platform
import tarfile
from urllib.request import urlopen

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--prefix', type=Path, default=Path.home() / '.local/share/skyseed/etcd')
args = parser.parse_args()
version = '3.6.5'
arch = {'x86_64': 'amd64', 'aarch64': 'arm64'}.get(platform.machine())
if platform.system() != 'Linux' or arch is None:
    parser.error('install etcd manually on this platform and set SKYSEED_ETCD_EXECUTABLE')
name = f'etcd-v{version}-linux-{arch}'
base = f'https://github.com/etcd-io/etcd/releases/download/v{version}/'
data = urlopen(base + name + '.tar.gz').read()
checksums = urlopen(base + 'SHA256SUMS').read().decode()
expected = next(line.split()[0] for line in checksums.splitlines() if line.split()[-1].lstrip('*') == name + '.tar.gz')
if hashlib.sha256(data).hexdigest() != expected:
    raise RuntimeError('etcd release checksum mismatch')
args.prefix.mkdir(parents=True, exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
    for binary in ('etcd', 'etcdctl'):
        destination = args.prefix / binary
        destination.write_bytes(archive.extractfile(name + '/' + binary).read())
        destination.chmod(0o755)
print(args.prefix / 'etcd')
