"""Static package metadata. Reading or fetching an app never imports it."""

from dataclasses import dataclass
import hashlib
from importlib import metadata
from pathlib import Path, PureWindowsPath
import re
import subprocess
import sys
import tomllib
from urllib.parse import urlsplit

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version


@dataclass(frozen=True)
class Package:
    path: Path
    name: str
    version: str
    title: str
    dependencies: tuple

    def locked(self):
        digest = hashlib.sha256()
        files = [self.path / 'pyproject.toml', *sorted((self.path / self.name).rglob('*'))]
        for path in files:
            if '__pycache__' in path.parts or path.suffix == '.pyc':
                continue
            if path.is_symlink():
                raise ValueError(f'App source cannot contain symbolic links: {path}')
            if path.is_file():
                digest.update(str(path.relative_to(self.path)).encode() + b'\0')
                digest.update(hashlib.sha256(path.read_bytes()).digest())
        source, commit = 'apps/' + self.name, None
        if (self.path / '.git').exists():
            commit = git(self.path, 'rev-parse', 'HEAD').strip()
            if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', commit):
                raise ValueError('Git did not return a full commit SHA')
            result = subprocess.run(['git', '-C', str(self.path), 'config', '--get', 'remote.origin.url'], capture_output=True, text=True)
            if result.returncode == 0:
                source = result.stdout.strip()
        return {'version': self.version, 'source': source, 'commit': commit, 'sha256': digest.hexdigest()}


def portable_lock_entry(name, entry):
    """Read older local locks without requiring their original absolute location."""
    source = entry['source']
    path = PureWindowsPath(source) if PureWindowsPath(source).is_absolute() else Path(source)
    if path.is_absolute() and path.name == name and path.parent.name == 'apps':
        return {**entry, 'source': 'apps/' + name}
    return entry


def git(path, *args):
    result = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError('Git failed: ' + result.stderr.strip())
    return result.stdout


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('https', 'ssh') or not parsed.hostname or not parsed.path.strip('/') or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('Use an HTTPS or ssh:// Git URL without a password, query or fragment')
    if parsed.scheme == 'https' and parsed.username:
        raise ValueError('Keep Git credentials out of application URLs; use an ssh:// URL and SSH authentication for private repositories')
    if any(ord(char) < 33 for char in url):
        raise ValueError('Invalid Git URL')
    return url


def read_package(path):
    from .cli.project import valid_name
    path = Path(path).resolve()
    file = path / 'pyproject.toml'
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 1024 * 1024:
        raise ValueError(f'Missing or invalid pyproject.toml in {path}')
    with file.open('rb') as stream:
        document = tomllib.load(stream)
    project = document.get('project', {})
    tool = document.get('tool', {})
    if not isinstance(project, dict) or not isinstance(tool, dict):
        raise ValueError('project and tool metadata must be TOML tables')
    options = tool.get('evlet', {})
    if not isinstance(options, dict):
        raise ValueError('tool.evlet metadata must be a TOML table')
    if options.get('app') is not True:
        raise ValueError('App metadata must declare [tool.evlet] app = true')
    if set(options) - {'app', 'title'}:
        raise ValueError('Unknown tool.evlet option; supported options are app and title')
    name = valid_name(project.get('name'))
    reserved = {'evlet', 'uvicorn', 'packaging', 'psycopg', 'psycopg_pool', 'boto3', 'botocore'}
    if name in reserved or name in sys.stdlib_module_names or name in metadata.packages_distributions():
        raise ValueError('Application name conflicts with the framework, Python standard library or an installed Python package')
    version = project.get('version')
    if not isinstance(version, str):
        raise ValueError('App metadata requires a static project.version')
    Version(version)
    requires_python = project.get('requires-python')
    if not isinstance(requires_python, str) or not requires_python:
        raise ValueError('App metadata requires project.requires-python')
    if not SpecifierSet(requires_python).contains('.'.join(map(str, sys.version_info[:3])), prereleases=True):
        raise ValueError(f'{name} requires Python {requires_python}')
    dependencies = project.get('dependencies', [])
    if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
        raise ValueError('project.dependencies must be an array of requirement strings')
    requirements = tuple(Requirement(item) for item in dependencies)
    if not any(canonicalize_name(item.name) == 'evlet' and not item.url and (not item.marker or item.marker.evaluate()) for item in requirements):
        raise ValueError('Apps must declare an active Evlet dependency, such as evlet>=0.1.2')
    for requirement in requirements:
        if requirement.url:
            raise ValueError('Direct URL dependencies are not supported; fetch apps separately and declare version constraints')
        if canonicalize_name(requirement.name) == 'evlet' and (not requirement.marker or requirement.marker.evaluate()):
            if not requirement.specifier.contains(metadata.version('evlet'), prereleases=True):
                raise ValueError(f'{name} requires {requirement}; installed Evlet is {metadata.version("evlet")}')
    if (path / name).is_symlink():
        raise ValueError('App package directory cannot be a symbolic link')
    for relative in (f'{name}/__init__.py', f'{name}/hooks.py'):
        target = path / relative
        if not target.is_file() or target.is_symlink() or not target.resolve().is_relative_to(path):
            raise ValueError(f'App is missing a regular {relative}')
    title = options.get('title', name)
    if not isinstance(title, str) or not title.strip():
        raise ValueError('App title must be nonempty text')
    package = Package(path, name, version, title, requirements)
    package.locked()  # Inspect source paths without importing Python.
    return package


def resolve_packages(root, names):
    """Resolve local apps in dependency order and check installed Python dependencies."""
    from .cli.project import child
    available = {}
    for path in sorted((root / 'apps').iterdir()):
        if path.is_dir() and not path.name.startswith('.'):
            available[canonicalize_name(path.name)] = path.name
    result, visiting = {}, set()

    def visit(name):
        if name in visiting:
            raise ValueError(f'Circular app dependency involving {name}')
        if name in result:
            return result[name]
        visiting.add(name)
        package = read_package(child(root, 'apps', name))
        if package.name != name:
            raise ValueError(f'App directory {name} does not match package name {package.name}')
        for requirement in package.dependencies:
            if requirement.marker and not requirement.marker.evaluate():
                continue
            normalized = canonicalize_name(requirement.name)
            if normalized in available:
                if requirement.extras:
                    raise ValueError('Optional extras on local app dependencies are not supported')
                version = visit(available[normalized]).version
            else:
                try:
                    version = metadata.version(requirement.name)
                except metadata.PackageNotFoundError:
                    raise ValueError(f'Missing dependency {requirement}; fetch the app or install this Python dependency in the current environment') from None
                check_extras(requirement)
            if not requirement.specifier.contains(version, prereleases=True):
                raise ValueError(f'Unsatisfied dependency {requirement}; found {version}')
        visiting.remove(name)
        result[name] = package
        return package

    for name in names:
        visit(name)
    return tuple(result.values())


def check_extras(requirement, seen=None):
    seen = set() if seen is None else seen
    key = (canonicalize_name(requirement.name), tuple(sorted(requirement.extras)))
    if key in seen:
        return
    seen.add(key)
    distribution = metadata.distribution(requirement.name)
    if not requirement.extras <= set(distribution.metadata.get_all('Provides-Extra') or []):
        raise ValueError(f'Unknown dependency extras in {requirement}')
    for value in distribution.requires or ():
        item = Requirement(value)
        if item.marker and not any(item.marker.evaluate({'extra': extra}) for extra in ('', *requirement.extras)):
            continue
        if item.url:
            raise ValueError(f'Cannot validate direct dependency {item}')
        try:
            version = metadata.version(item.name)
        except metadata.PackageNotFoundError:
            raise ValueError(f'Missing dependency {item} required by {requirement}') from None
        if not item.specifier.contains(version, prereleases=True):
            raise ValueError(f'Unsatisfied dependency {item} required by {requirement}')
        check_extras(item, seen)
