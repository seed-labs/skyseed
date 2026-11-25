"""Check the active source and the SEED construction capability, without running it."""
from check_active_source import verify_source
from importlib.util import find_spec
from pathlib import Path


def main():
    verify_source(report=True)
    expected = Path(__file__).resolve().parents[1] / 'tools/__init__.py'
    spec = find_spec('tools')
    if spec is None or spec.origin is None or Path(spec.origin).resolve() != expected:
        raise RuntimeError('Repository Tools are not installed from this checkout. '
                           'Run: python -m pip install -e ./tools')
    import seedemu
    from seedemu import core
    if not hasattr(core, 'ExtensionNode') or not hasattr(core.AutonomousSystem, 'addExtensionNode'):
        raise RuntimeError(
            f'SEED checkout lacks ExtensionNode lifecycle support: {seedemu.__file__}. '
            'Use the extension-node revision and installation commands in README.md.'
        )
    from skyseed.emulator import SkyBase
    print(f'SEED source: {seedemu.__file__}')
    print(f'Construction interfaces: {SkyBase.__name__}, {core.ExtensionNode.__name__}')
    verify_source()


if __name__ == '__main__':
    main()
