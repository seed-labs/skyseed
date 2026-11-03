"""Check the active source and the SEED construction capability, without running it."""
from check_active_source import verify_source


def main():
    verify_source(report=True)
    import seedemu
    from seedemu.core import AutonomousSystem, ExtensionNode
    from skyseed.emulator import SkyBase
    if not hasattr(AutonomousSystem, 'addExtensionNode'):
        raise RuntimeError('SEED requires ExtensionNode lifecycle support')
    print(f'SEED source: {seedemu.__file__}')
    print(f'Construction interfaces: {SkyBase.__name__}, {ExtensionNode.__name__}')
    verify_source()


if __name__ == '__main__':
    main()
