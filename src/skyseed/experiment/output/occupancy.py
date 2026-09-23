"""Local process claims protect output replacement and permit computation with execution."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import fcntl
import tempfile


def _lock_file(output, scope):
    identity = sha256(str(Path(output).resolve()).encode()).hexdigest()
    path = Path(tempfile.gettempdir()) / f"skyseed-{identity}-{scope}.lock"
    return path.open("a+")


@contextmanager
def claim_output(output, *, replace=False, computation=False):
    """Claim before touching output. Replacement excludes all existing users."""
    with _lock_file(output, "occupancy") as occupancy:
        compute = _lock_file(output, "computation") if computation else None
        try:
            if compute is not None:
                fcntl.flock(compute, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(occupancy, (fcntl.LOCK_EX if replace else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            if compute is not None:
                compute.close()
            raise RuntimeError(f"experiment output is actively owned: {output}") from error
        try:
            # Preparation calls this after replacement; retaining SH permits run.
            yield lambda: fcntl.flock(occupancy, fcntl.LOCK_SH)
        finally:
            if compute is not None:
                compute.close()


@contextmanager
def claim_runtime(output):
    """Exclusive runtime use also protects independent deployment cleanup."""
    with _lock_file(output, 'runtime') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('runtime is active; cancel-runtime and await its cleanup first') from error
        yield
