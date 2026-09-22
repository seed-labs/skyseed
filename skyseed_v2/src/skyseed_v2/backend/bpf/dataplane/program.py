"""Build-time C/ELF preparation. Runner never imports this module."""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile

from skyseed_v2.backend.linux.elf import _link_objects
from .object_contract import _validated_elf

_INCLUDE = Path(__file__).with_name('include')
_DEFAULT = Path(__file__).with_name('default.bpf.c')


@dataclass(frozen=True)
class BpfProgram:
    """C sources (with local headers) or compatible linked BPF ELF objects.

    A sequence supports multiple translation units, linked by libbpf at preparation.
    No author Python is needed at execution. Omission selects the packaged default.
    """
    path: Path | tuple[Path, ...]
    include_dirs: tuple[Path, ...] = ()
    data: object = None
    authoring_directory: Path | None = None

    def __post_init__(self):
        from .data import RouteData
        if self.data is not None and not isinstance(self.data, RouteData):
            raise TypeError("data must be a RouteData definition")
        paths = (self.path,) if isinstance(self.path, (str, Path)) else tuple(self.path)
        if not paths:
            raise ValueError('BpfProgram needs C or object inputs')
        object.__setattr__(self, 'path', tuple(Path(p).expanduser().resolve() for p in paths))
        object.__setattr__(self, 'include_dirs', tuple(Path(p).expanduser().resolve() for p in self.include_dirs))
        if any(p.suffix not in {'.c', '.o'} for p in self.path):
            raise ValueError('BpfProgram inputs must be .c or .o files')


def _compiler():
    compiler = shutil.which('clang')
    if compiler is None:
        candidate = Path(sys.prefix) / 'bin/clang'
        if candidate.is_file():
            compiler = str(candidate)
    if compiler is None:
        raise RuntimeError('C DataPlane preparation requires Clang with the BPF target; see templates/dataplane/README.md')
    return compiler


def _prepare_program(selection=None):
    if selection is None:
        # Packaged inputs are immutable during a build; retained deliveries share
        # the prepared foundation. Custom sources are re-read for each new build.
        return _default_program()
    if not isinstance(selection, BpfProgram):
        raise TypeError('dataplane_program must be a BpfProgram or None')
    return _build_program(selection)


@lru_cache(maxsize=1)
def _default_program():
    return _build_program(BpfProgram(_DEFAULT))


def _build_program(selection):
    dependencies, objects, commands = {}, [], []
    generated = None
    if selection.data is not None:
        generated = selection.data.generate_headers(selection.authoring_directory or selection.path[0].parent / "generated").parent
    compiler = _compiler() if any(p.suffix == '.c' for p in selection.path) else None
    compiler_version = None if compiler is None else subprocess.check_output([compiler, '--version'], text=True).strip()
    with tempfile.TemporaryDirectory(prefix='skyseed-bpf-') as directory:
        root = Path(directory)
        for i, source in enumerate(selection.path):
            if source.suffix == '.o':
                objects.append(source)
                dependencies[str(source)] = sha256(source.read_bytes()).hexdigest()
                continue
            output, depfile = root / f'part-{i}.o', root / f'part-{i}.d'
            includes = [_INCLUDE, *([generated] if generated else []), Path(sys.prefix) / 'include', *selection.include_dirs]
            multiarch = sysconfig.get_config_var('MULTIARCH')
            if multiarch and (Path('/usr/include') / multiarch).is_dir():
                includes.append(Path('/usr/include') / multiarch)
            command = [compiler, '-target', 'bpfel', '-O2', '-g', '-c', str(source), '-o', str(output),
                       '-MD', '-MF', str(depfile), *[arg for p in includes for arg in ('-I', str(p))]]
            # Let Clang print its ordinary diagnostics. No source/instruction limits.
            subprocess.run(command, check=True)
            commands.append([str(a).replace(str(root), '<build>') for a in command])
            # Clang's make dependency format escapes spaces, backslashes and #.
            dependency_text = depfile.read_text().replace('\\\n', '')
            for name in shlex.split(dependency_text.split(':', 1)[1]):
                path = Path(name.replace('$$', '$')).resolve()
                dependencies[str(path)] = sha256(path.read_bytes()).hexdigest()
            objects.append(output)
        if len(objects) > 1:
            output = root / 'linked.o'
            _link_objects(objects, output)
        else:
            output = objects[0]
        content = output.read_bytes()
    encoded, digest = base64.b64encode(content).decode(), sha256(content).hexdigest()
    _, declaration_json = _validated_elf(encoded, digest)
    declaration = json.loads(declaration_json)
    if selection.data is not None and declaration.get("data") != selection.data.to_data():
        raise ValueError("program does not include the selected generated data declaration")
    if selection.data is None and declaration.get("data") is not None:
        # Compatible sealed ELF carries its definition; C authors normally supply data=.
        from .data import RouteData
        RouteData.from_data(declaration["data"])
    return {'elf': encoded, 'sha256': digest,
            'declaration': declaration,
            'provenance': {'inputs': [str(p) for p in selection.path], 'dependencies': dependencies,
                           'compiler': compiler_version, 'commands': commands}}


def prepare_program(program=None):
    """Compile/inspect offline and return the sealed object and declarations."""
    from copy import deepcopy
    return deepcopy(_prepare_program(program))
