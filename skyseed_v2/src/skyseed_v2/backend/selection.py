"""Explicit experiment forwarding choice; independent of simulation values."""
from skyseed_v2.configuration.forwarding import ForwardingBackend, _backend


def _emulator_backend(emulator):
    return _backend(getattr(emulator, "_skyseed_forwarding_backend", ForwardingBackend.BPF))


def _validate_program(backend, program):
    if backend == ForwardingBackend.LINUX and program is not None:
        raise ValueError("Linux forwarding is incompatible with dataplane_program (BPF)")
