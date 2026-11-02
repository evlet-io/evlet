"""Bundle maintained examples and documentation without copying them into projects."""

from pathlib import Path
import shutil
from setuptools.command.build_py import build_py


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
        for name in ('app.py', 'models.py', 'migrate.py', 'settings.py', 'ui.py'):
            shutil.copy2(root / 'examples/playground' / name, playground / name)
        shutil.copytree(root / 'examples/playground/portal', playground / 'portal', dirs_exist_ok=True)
        docs = resources / 'docs'
        docs.mkdir(parents=True, exist_ok=True)
        for path in (root / 'docs').glob('*.md'):
            shutil.copy2(path, docs / path.name)
