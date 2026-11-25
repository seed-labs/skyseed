"""Check Scene, Emulator and offline P&C boundaries in the active source root."""
import ast
from check_active_source import verify_source


def main():
    root = verify_source()
    errors = []
    for path in root.rglob('*.py'):
        is_scene = path.is_relative_to(root / 'scene')
        is_pc = path.is_relative_to(root / 'pc')
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module else []
                if is_scene and node.level > 1:
                    errors.append(f'{path}:{node.lineno}: Scene imports outside its module')
            for name in names:
                uses_seed = name == 'seedemu' or name.startswith('seedemu.')
                uses_runtime = name.startswith(('skyseed.emulator', 'skyseed.runner', 'skyseed.store'))
                if is_scene and (uses_seed or uses_runtime or name.startswith('skyseed.pc')):
                    errors.append(f'{path}:{node.lineno}: Scene must not depend on SEED/Emulator/P&C')
                if is_pc and (uses_seed or uses_runtime):
                    errors.append(f'{path}:{node.lineno}: offline P&C must not depend on SEED/Emulator')
                if name.startswith('skyseed.') and name.split('.')[1] not in {'scene', 'emulator', 'pc', 'runner', 'store'}:
                    errors.append(f'{path}:{node.lineno}: unimplemented/legacy dependency {name}')
    if errors:
        raise SystemExit('\n'.join(errors))
    print('Scene, Emulator and offline P&C dependency boundaries passed.')


if __name__ == '__main__':
    main()
