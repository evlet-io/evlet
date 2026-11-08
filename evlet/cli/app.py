"""Create or statically fetch reusable Python application packages."""

from importlib.resources import files
from pathlib import Path
import os
import subprocess
import tempfile

from ..packages import read_package, validate_url
from .project import child, valid_name


def create_app(root, name, *, legacy=False):
    valid_name(name)
    target = child(root, 'apps', name)
    if target.exists():
        raise ValueError(f'App already exists: {name}')
    with tempfile.TemporaryDirectory(prefix='.create-', dir=root / 'apps') as directory:
        stage = Path(directory) / name
        stage.mkdir()
        templates = files('evlet.resources').joinpath('templates/app' if legacy else 'templates/app_flat')
        for template in templates.rglob('*.tpl'):
            relative = str(template.relative_to(templates)).removesuffix('.tpl').replace('__app__', name)
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(template.read_text().replace('{{name}}', name).replace('{{Class}}', name[0].upper() + name[1:]).replace('{{title}}', name.replace('_', ' ').title()), encoding='utf-8')
        read_package(stage)
        os.rename(stage, target)
    return target


def fetch_app(root, url, *, return_package=False):
    validate_url(url)
    with tempfile.TemporaryDirectory(prefix='.fetch-', dir=root / 'apps') as directory:
        stage = Path(directory) / 'source'
        # Disable clone-time hooks and checkout filters inherited from local Git config.
        env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_CONFIG')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0', GIT_LFS_SKIP_SMUDGE='1')
        result = subprocess.run(['git', '-c', 'core.hooksPath=' + os.devnull, '-c', 'protocol.file.allow=never', '-c', 'protocol.ext.allow=never', 'clone', '--no-recurse-submodules', '--', url, str(stage)], env=env, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise ValueError('Git clone failed: ' + result.stderr.strip() + '\nFor private repositories, use an ssh:// URL with SSH authentication; global HTTPS credential helpers are disabled during fetch.')
        if (stage / '.gitmodules').exists():
            raise ValueError('App submodules are not supported; distribute a complete app repository')
        package = read_package(stage)
        target = child(root, 'apps', package.name)
        if target.exists():
            raise ValueError(f'App already exists: {package.name}')
        revision = package.locked()['commit']
        os.rename(stage, target)
    dependencies = ', '.join(map(str, package.dependencies)) or 'none'
    if return_package: return read_package(target)
    return f'Fetched {package.name} {package.version} at {revision}\nDeclared dependencies: {dependencies}\nStatic validation passed; no app code was run. Install only code you trust.'


def get_app(root, url, *, trust=False, confirm=None):
    """evlet app --get: fetch statically, then prepare and build only after explicit trust."""
    from ..builds import prepare
    package = fetch_app(root, url, return_package=True)
    print(f'Fetched {package.name} {package.version} at {package.locked()["commit"]}; no app code has run')
    if not trust and not (confirm and confirm('Trust and prepare ' + package.name + ' from ' + url + ' and its declared dependencies?')):
        raise ValueError('Fetched but not built: review source, then run evlet build --trust')
    _, manifest = prepare(root, prepare_dependencies(root, [package.name]))
    print('Built generation: ' + manifest['fingerprint'])
    return package


def prepare_dependencies(root, names):
    """Run reviewed package hooks only in this project's environment."""
    import sys
    from importlib import metadata
    from packaging.utils import canonicalize_name
    from .setup import python_in
    if Path(sys.prefix).resolve() != (root / '.venv').resolve():
        # Existing projects keep their explicit environment; never pip into it implicitly.
        from ..packages import resolve_packages
        return resolve_packages(root, names)
    available = {canonicalize_name(path.name): path for path in (root / 'apps').iterdir() if path.is_dir() and not path.name.startswith('.')}
    packages, requirements, visiting = {}, {}, set()
    def visit(name):
        if name in visiting: raise ValueError('Circular app dependency involving ' + name)
        if name in packages: return
        visiting.add(name)
        package = read_package(root / 'apps' / name)
        for requirement in package.dependencies:
            if requirement.marker and not requirement.marker.evaluate(): continue
            normalized = canonicalize_name(requirement.name)
            if normalized in available:
                visit(available[normalized].name)
            elif normalized != 'evlet':
                from ..packages import check_extras
                try:
                    if not requirement.specifier.contains(metadata.version(requirement.name), prereleases=True): raise ValueError('Version mismatch')
                    check_extras(requirement)
                except (metadata.PackageNotFoundError, ValueError): requirements[str(requirement)] = requirement
        visiting.remove(name)
        packages[name] = package
    for name in names: visit(name)
    with tempfile.TemporaryDirectory(prefix='.dependencies-', dir=root / '.venv') as directory:
        constraints = Path(directory) / 'constraints.txt'
        constraints.write_text('evlet==' + metadata.version('evlet') + '\n')
        if requirements:
            result = subprocess.run([str(python_in(root)), '-m', 'pip', 'install', '--disable-pip-version-check', '-c', str(constraints), *requirements], capture_output=True, text=True)
            if result.returncode: raise ValueError('Project dependency preparation failed: ' + result.stderr.strip())
        for package in packages.values():
            from ..packages import owned_distribution
            if owned_distribution(package.name, package.path) and metadata.version(package.name) == package.version: continue
            result = subprocess.run([str(python_in(root)), '-m', 'pip', 'install', '--disable-pip-version-check', '--no-deps', '-e', str(package.path)], capture_output=True, text=True)
            if result.returncode: raise ValueError('App package preparation failed: ' + result.stderr.strip())
    from ..packages import resolve_packages
    return resolve_packages(root, names)
