"""Build the packaged label program once, outside scheduled execution."""
from pathlib import Path
from importlib.resources import as_file, files
import shutil
import subprocess
import sys
import tempfile


def compile_label_program(*, clang: str | None = None) -> bytes:
    compiler = clang or shutil.which("clang")
    if compiler is None:
        candidate = Path(sys.executable).parent / "clang"
        if candidate.exists():
            compiler = str(candidate)
    if compiler is None:
        raise FileNotFoundError("label program compilation requires clang with the BPF target")
    resource = files(__package__).joinpath("label_forwarding.bpf.c")
    with as_file(resource) as source, tempfile.TemporaryDirectory(prefix="skyseed-bpf-") as directory:
        output = Path(directory) / "label.o"
        command = [compiler, "-target", "bpfel" if sys.byteorder == "little" else "bpfeb",
                   "-O2", "-g", "-Wall", "-Werror"]
        for include in Path("/usr/include").glob("*-linux-gnu"):
            command.extend(["-I", str(include)])
        command.extend(["-c", str(source), "-o", str(output)])
        subprocess.run(command, check=True, capture_output=True)
        return output.read_bytes()
