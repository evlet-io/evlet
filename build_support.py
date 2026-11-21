"""Bundle maintained examples and documentation without copying them into projects."""

import ast
from pathlib import Path
import shutil
from setuptools.command.build_py import build_py


def playground_files(folder):
    """Return app.py, the sibling modules it imports (transitively), and the Playground scripts."""
    seen, todo = set(), ['app']
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        for node in ast.walk(ast.parse((folder / f'{name}.py').read_text(encoding='utf-8'))):
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                modules = [node.module.split('.')[0]] if node.module else [alias.name for alias in node.names]
                todo += [module for module in modules if (folder / f'{module}.py').is_file()]
    return sorted(f'{name}.py' for name in seen) + sorted(path.name for path in folder.glob('*.js'))


class BuildPy(build_py):
    def run(self):
        super().run()
        root = Path(__file__).parent
        if not (root / 'evlet/ui/static/index.html').is_file():
            raise RuntimeError('Build the browser assets with npm --prefix frontend run build before packaging Evlet')
        resources = Path(self.build_lib) / 'evlet/resources'
        playground = resources / 'playground'
        playground.mkdir(parents=True, exist_ok=True)
        (playground / '__init__.py').write_text('"""Bundled framework examples."""\n')
        for name in playground_files(root / 'examples/playground'):
            shutil.copy2(root / 'examples/playground' / name, playground / name)
        shutil.copytree(root / 'examples/playground/portal', playground / 'portal', dirs_exist_ok=True)
        shutil.copytree(root / 'examples/playground/site', playground / 'site', dirs_exist_ok=True)
        docs = resources / 'docs'
        docs.mkdir(parents=True, exist_ok=True)
        for path in (root / 'docs').glob('*.md'):
            shutil.copy2(path, docs / path.name)
