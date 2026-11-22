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
            destination.write_text(template.read_text().replace('{{name}}', name).replace('{{table}}', name + 'item').replace('{{Class}}', name[0].upper() + name[1:]).replace('{{title}}', name.replace('_', ' ').title()), encoding='utf-8')
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


def get_app(root, url, *, trust=False, confirm=None, require_hashes=None):
    """evlet app --get: fetch statically, then prepare and build only after explicit trust."""
    from ..builds import prepare
    package = fetch_app(root, url, return_package=True)
    print(f'Fetched {package.name} {package.version} at {package.locked()["commit"]}; no app code has run')
    if not trust and not (confirm and confirm('Trust and prepare ' + package.name + ' from ' + url + ' and its declared dependencies?')):
        raise ValueError('Fetched but not built: review source, then run evlet build --trust')
    _, manifest = prepare(root, prepare_dependencies(root, [package.name], require_hashes=require_hashes))
    print('Built generation: ' + manifest['fingerprint'])
    return package


# An app may ship this pip requirements file, every line pinned with == and --hash=sha256:..., for --require-hashes.
LOCK_FILE = 'requirements.lock'


def pinned(requirement):
    """Whether a declared requirement names one exact version (== without a wildcard, or ===)."""
    return any(spec.operator == '===' or (spec.operator == '==' and not spec.version.endswith('*')) for spec in requirement.specifier)


def require_hashes_wanted(value=None):
    """--require-hashes, or EVLET_REQUIRE_HASHES=1 for commands without the option (evlet install and migrate)."""
    if value is not None:
        return bool(value)
    return os.environ.get('EVLET_REQUIRE_HASHES', '').strip().lower() in ('1', 'true', 'yes')


def unpinned_warning(owners):
    """One warning naming the apps whose third-party dependencies pip resolves at install time, or None."""
    loose = {app: [str(item) for item in items if not pinned(item)] for app, items in owners.items()}
    loose = {app: items for app, items in loose.items() if items}
    if not loose:
        return None
    return ('Warning: unpinned app dependencies: ' + '; '.join(f'{app}: ' + ', '.join(items) for app, items in sorted(loose.items()))
            + '. pip installs whatever these ranges allow today, so two servers can run different third-party code. '
            f'Ship {LOCK_FILE} with hashes in the app and install with --require-hashes (see docs/production-operations.md).')


def prepare_dependencies(root, names, *, require_hashes=None):
    """Run reviewed package hooks only in this project's environment.

    Third-party dependencies are installed from the declared ranges, with a warning for each unpinned one.
    require_hashes (or EVLET_REQUIRE_HASHES=1) installs them only from the requirements.lock each app ships,
    with pip --require-hashes --no-deps, so exactly the reviewed files are installed."""
    import sys
    from importlib import metadata
    from packaging.utils import canonicalize_name
    from .setup import python_in
    if Path(sys.prefix).resolve() != (root / '.venv').resolve():
        # Existing projects keep their explicit environment; never pip into it implicitly.
        from ..packages import resolve_packages
        return resolve_packages(root, names)
    require_hashes = require_hashes_wanted(require_hashes)
    available = {canonicalize_name(path.name): path for path in (root / 'apps').iterdir() if path.is_dir() and not path.name.startswith('.')}
    packages, requirements, owners, visiting = {}, {}, {}, set()
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
                except (metadata.PackageNotFoundError, ValueError):
                    requirements[str(requirement)] = requirement
                    owners.setdefault(package.name, []).append(requirement)
        visiting.remove(name)
        packages[name] = package
    for name in names: visit(name)
    from ..packages import owned_distribution
    editable = [package for package in packages.values()
                if not (owned_distribution(package.name, package.path) and metadata.version(package.name) == package.version)]
    if requirements and require_hashes:
        missing = sorted(app for app in owners if not (packages[app].path / LOCK_FILE).is_file())
        if missing:
            raise ValueError(f'--require-hashes needs a {LOCK_FILE} with hashes in ' + ', '.join(missing)
                             + ' for: ' + ', '.join(str(item) for app in missing for item in owners[app]) + '. Nothing was installed')
    elif requirements:
        warning = unpinned_warning(owners)
        if warning:
            print(warning, file=sys.stderr)
    if not (requirements or editable):
        # Nothing to install: leave .venv untouched (a dedicated service account may only read it).
        from ..packages import resolve_packages
        return resolve_packages(root, names)
    with tempfile.TemporaryDirectory(prefix='.dependencies-', dir=root / '.venv') as directory:
        constraints = Path(directory) / 'constraints.txt'
        constraints.write_text('evlet==' + metadata.version('evlet') + '\n')
        pip = [str(python_in(root)), '-m', 'pip', 'install', '--disable-pip-version-check']
        if requirements and require_hashes:
            for app in sorted(owners):
                # Every file is named with its hash and dependencies are not resolved: the lock is the whole set.
                result = subprocess.run([*pip, '--require-hashes', '--no-deps', '-r', str(packages[app].path / LOCK_FILE)], capture_output=True, text=True)
                if result.returncode: raise ValueError(f'Installing {app}/{LOCK_FILE} with --require-hashes failed: ' + result.stderr.strip())
            from importlib import invalidate_caches
            invalidate_caches()
            for requirement in requirements.values():
                try:
                    found = metadata.version(requirement.name)
                except metadata.PackageNotFoundError:
                    found = None
                if found is None or not requirement.specifier.contains(found, prereleases=True):
                    raise ValueError(f'The {LOCK_FILE} files do not provide {requirement} (installed: {found or "none"})')
        elif requirements:
            result = subprocess.run([*pip, '-c', str(constraints), *requirements], capture_output=True, text=True)
            if result.returncode: raise ValueError('Project dependency preparation failed: ' + result.stderr.strip())
        for package in editable:
            result = subprocess.run([*pip, '--no-deps', '-e', str(package.path)], capture_output=True, text=True)
            if result.returncode: raise ValueError('App package preparation failed: ' + result.stderr.strip())
    from ..packages import resolve_packages
    return resolve_packages(root, names)
