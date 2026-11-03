"""Check the new Scene/Emulator dependency boundary using the active source root."""
import ast
from check_active_source import verify_source


def main():
    root = verify_source()
    errors = []
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module] if node.module else []
                if node.level and path.is_relative_to(root / 'scene') and node.level > 1:
                    errors.append(f'{path}:{node.lineno}: Scene imports outside its module')
            for name in names:
                if path.is_relative_to(root / 'scene') and (
                    name == 'seedemu' or name.startswith('seedemu.') or name.startswith('skyseed.emulator')
                ):
                    errors.append(f'{path}:{node.lineno}: Scene must not depend on SEED/Emulator')
                if name.startswith('skyseed.') and name.split('.')[1] not in {'scene', 'emulator'}:
                    errors.append(f'{path}:{node.lineno}: unimplemented/legacy dependency {name}')
    if errors:
        raise SystemExit('\n'.join(errors))
    print('Scene and Emulator dependency boundaries passed.')


if __name__ == '__main__':
    main()
