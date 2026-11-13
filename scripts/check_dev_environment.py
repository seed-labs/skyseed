"""Check the active source and the SEED construction capability, without running it."""
from check_active_source import verify_source


def main():
    verify_source(report=True)
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
