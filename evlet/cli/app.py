"""Create or statically fetch reusable Python application packages."""

from importlib.resources import files
from pathlib import Path
import os
import subprocess
import tempfile

from ..packages import read_package, validate_url
from .project import child, valid_name


def create_app(root, name):
    valid_name(name)
    target = child(root, 'apps', name)
    if target.exists():
        raise ValueError(f'App already exists: {name}')
    with tempfile.TemporaryDirectory(prefix='.create-', dir=root / 'apps') as directory:
        stage = Path(directory) / name
        stage.mkdir()
        templates = files('evlet.resources').joinpath('templates/app')
        for template in templates.rglob('*.tpl'):
            relative = str(template.relative_to(templates)).removesuffix('.tpl').replace('__app__', name)
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(template.read_text().replace('{{name}}', name).replace('{{Class}}', ''.join(word.capitalize() for word in name.split('_'))), encoding='utf-8')
        read_package(stage)
        os.rename(stage, target)
    return target


def fetch_app(root, url):
    validate_url(url)
    with tempfile.TemporaryDirectory(prefix='.fetch-', dir=root / 'apps') as directory:
        stage = Path(directory) / 'source'
        # Disable clone-time hooks and checkout filters inherited from local Git config.
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_CONFIG')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0', GIT_LFS_SKIP_SMUDGE='1')
        result = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'protocol.file.allow=never', '-c', 'protocol.ext.allow=never', 'clone', '--no-recurse-submodules', '--', url, str(stage)], env=env, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise ValueError('Git clone failed: ' + result.stderr.strip())
        if (stage / '.gitmodules').exists():
            raise ValueError('App submodules are not supported; distribute a complete app repository')
        package = read_package(stage)
        target = child(root, 'apps', package.name)
        if target.exists():
            raise ValueError(f'App already exists: {package.name}')
        revision = package.locked()['commit']
        os.rename(stage, target)
    dependencies = ', '.join(map(str, package.dependencies)) or 'none'
    return f'Fetched {package.name} {package.version} at {revision}\nDeclared dependencies: {dependencies}\nStatic validation passed; no app code was run. Install only code you trust.'
