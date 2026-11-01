"""Create a project and its isolated Python environment atomically."""

from pathlib import Path
from importlib import metadata
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


def create_environment(root, framework=None):
    venv.EnvBuilder(with_pip=True).create(root / '.venv')
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
    os.execve(str(executable), [str(executable), '-m', 'evlet', *arguments], env)


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
