"""The normal Evlet import and local authentication do not require the OIDC extra."""

import os
from pathlib import Path
import subprocess
import sys
import tomllib


def test_oidc_dependencies_are_optional_and_loaded_only_for_enabled_flow(tmp_path):
    metadata = tomllib.loads((Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text())['project']
    assert not any('authlib' in item.lower() or 'joserfc' in item.lower() for item in metadata['dependencies'])
    assert metadata['optional-dependencies']['oidc'] == ['Authlib>=1.9.5,<2', 'joserfc>=1.8,<2']
    source = '''
import importlib.abc, sys
class NoOIDC(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('authlib', 'joserfc'):
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, NoOIDC())
from evlet import Application, Database, LocalAuth, Registry
from evlet.auth import OIDC
from pathlib import Path
db = Database(Path(sys.argv[1])/'optional.sqlite3', Registry())
auth = LocalAuth(db, origins=('https://desk.example',))
app = Application(db, auth=auth)
assert not any(name.startswith(('authlib', 'joserfc')) for name in sys.modules)
auth.oidc = OIDC('https://company.example', 'test', 'https://desk.example/_/oidc/callback', {'subject':'editor'})
try:
    Application(db, auth=auth)
except ValueError as error:
    assert 'evlet[oidc]' in str(error)
else:
    raise AssertionError('Missing optional dependency should have a clear startup error')
db.close()
'''
    answer = subprocess.run([sys.executable, '-c', source, str(tmp_path)], capture_output=True, text=True,
                            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, timeout=20)
    assert answer.returncode == 0, answer.stderr


def test_oidc_lock_is_hash_pinned_and_matches_optional_and_shared_requirements():
    import re
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / 'pyproject.toml').read_text())['project']
    pins = {}
    lines = (root / 'requirements-oidc.lock').read_text().replace('\\\n', ' ').splitlines()
    for line in lines:
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        declaration, *hashes = line.split('--hash=sha256:')
        requirement = Requirement(declaration.strip())
        assert hashes and all(re.fullmatch('[a-f0-9]{64}', value.strip()) for value in hashes)
        specs = list(requirement.specifier)
        assert len(specs) == 1 and specs[0].operator == '=='
        pins[canonicalize_name(requirement.name)] = specs[0].version
    assert set(pins) == {'authlib', 'joserfc', 'cryptography', 'cffi', 'pycparser'}
    for text in project['optional-dependencies']['oidc']:
        requirement = Requirement(text)
        assert requirement.specifier.contains(pins[canonicalize_name(requirement.name)])
    shared = dict(re.findall(r'^([a-zA-Z0-9_-]+)==([^\s;]+)', (root / 'requirements-dev.lock').read_text(), re.M))
    for name in pins.keys() & shared.keys():
        assert pins[name] == shared[name], f'{name} must not replace the core CI dependency with another version'
