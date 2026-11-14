"""Create a project and its isolated Python environment atomically."""

from contextlib import contextmanager
from pathlib import Path
from importlib import metadata, resources
import json
import os
import shutil
import subprocess
import sys
import venv
from urllib.parse import urlsplit
from urllib.request import url2pathname


def python_in(root):
    return root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


@contextmanager
def installer_bootstrap(python):
    """Use the release's hash-locked installer before installing framework code."""
    with resources.as_file(resources.files('evlet.resources') / 'bootstrap.lock') as lock:
        yield [str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--upgrade',
               '--only-binary=:all:', '--require-hashes', '-r', str(lock)]


def create_environment(root, framework=None):
    venv.EnvBuilder(with_pip=True).create(root / '.venv')
    with installer_bootstrap(python_in(root)) as command:
        result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Project installer preparation failed: ' + result.stderr.strip())
    if framework is None:
        source = Path(__file__).resolve().parents[2]
        distribution = metadata.distribution('evlet')
        direct = json.loads(distribution.read_text('direct_url.json') or '{}')
        url = urlsplit(direct.get('url', ''))
        local = Path(url2pathname(url.path)) if url.scheme == 'file' and url.netloc in ('', 'localhost') else None
        framework = (str(source) if (source / 'pyproject.toml').is_file() else str(local) if local is not None and local.exists() else 'evlet==' + distribution.version)
    result = subprocess.run([str(python_in(root)), '-m', 'pip', 'install', '--disable-pip-version-check', str(framework)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Project environment preparation failed: ' + result.stderr.strip())
    subprocess.run([str(python_in(root)), '-I', '-c', 'import evlet, uvicorn, packaging'], check=True, capture_output=True)
    (root / '.venv' / 'evlet-project.json').write_text(json.dumps({'version': 1}))


def environment_version(root):
    """The Evlet version installed in the project's .venv, read from its dist-info without importing it."""
    found = []
    for pattern in ('lib/python*/site-packages/evlet-*.dist-info/METADATA', 'Lib/site-packages/evlet-*.dist-info/METADATA'):
        for path in (Path(root) / '.venv').glob(pattern):
            try:
                headers = dict(line.split(': ', 1) for line in path.read_text(encoding='utf-8').split('\n\n', 1)[0].splitlines() if ': ' in line)
            except (OSError, UnicodeError):
                continue
            if headers.get('Name', '').lower() == 'evlet' and headers.get('Version'):
                found.append(headers['Version'])
    if not found:
        return None
    from packaging.version import InvalidVersion, Version
    try:
        return max(found, key=Version)
    except InvalidVersion:
        return found[0]


def version_note(root):
    """A warning when this evlet and the project's .venv differ, else None."""
    from ..framework import version
    mine, theirs = version(), environment_version(root)
    if theirs is None or theirs == mine:
        return None
    from packaging.version import InvalidVersion, Version
    note = f'This evlet is {mine}, but the project in {root} has Evlet {theirs} in its .venv. The command runs with {theirs}.'
    try:
        newer = Version(mine) > Version(theirs)
    except InvalidVersion:
        newer = False
    return note + ' Run evlet upgrade INSTANCE to update the project.' if newer else note


def delegate(root, arguments):
    """Replace this process, retaining its arguments, exit status and signals."""
    marker = root / '.venv' / 'evlet-project.json'
    if not marker.is_file():
        return  # Legacy projects retain their selected interpreter.
    executable = python_in(root)
    if Path(sys.prefix).resolve() == (root / '.venv').resolve():
        return
    if not executable.is_file():
        raise ValueError('Project environment is incomplete; restore .venv before running commands')
    env = dict(os.environ)
    if env.get('EVLET_DELEGATED_PROJECT') == str(root):
        raise ValueError('Project environment delegation did not select its interpreter')
    env['EVLET_DELEGATED_PROJECT'] = str(root)
    note = version_note(root)
    if note:
        print(note, file=sys.stderr)
        sys.stderr.flush()  # exec discards unwritten output
    # -P keeps the working directory off sys.path so local files cannot shadow modules.
    os.execve(str(executable), [str(executable), '-P', '-m', 'evlet', *arguments], env)


def setup(directory, *, framework=None):
    target = Path(directory).absolute()
    target.mkdir()
    try:
        (target / 'apps').mkdir()
        (target / 'instances').mkdir()
        create_environment(target, framework)
    except BaseException:
        # target was exclusively created by this call; never remove an existing project.
        shutil.rmtree(target)
        raise
    return target
