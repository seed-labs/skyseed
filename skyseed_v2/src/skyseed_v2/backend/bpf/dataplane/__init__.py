"""Public preparation and logical attachment/data declarations, resolved lazily."""
from importlib import import_module

_MODULES = {
    **{n: '.data' for n in ('RouteData', 'Record', 'Scalar', 'U8', 'U16', 'U32', 'U64', 'I32', 'I64', 'BOOL', 'EXIT', 'NODE')},
    **{n: '.program' for n in ('BpfProgram', 'prepare_program')},
    **{n: '.scopes' for n in ('AttachmentScope', 'DataPlaneBinding', 'DataPlane')},
}
__all__ = list(_MODULES)


def __getattr__(name):
    if name not in _MODULES:
        raise AttributeError(name)
    value = getattr(import_module(_MODULES[name], __name__), name)
    globals()[name] = value
    return value
