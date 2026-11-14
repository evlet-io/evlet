"""Installer preparation ordering and failure isolation, without network installs."""

from pathlib import Path
import subprocess

import pytest

from evlet.cli import setup as setting


@pytest.fixture
def installer(monkeypatch):
    calls = []
    monkeypatch.setattr(setting.venv.EnvBuilder, 'create', lambda self, path: Path(path).mkdir())

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    monkeypatch.setattr(setting.subprocess, 'run', run)
    return calls


def test_setup_bootstraps_from_the_packaged_lock_before_the_framework(tmp_path, installer):
    target = setting.setup(tmp_path / 'project', framework='/local/reviewed.whl')
    with setting.installer_bootstrap(setting.python_in(target)) as command:
        assert installer[0] == command
        lock = Path(command[-1])
        assert lock.parent.name == 'resources'
        assert 'pip==26.2' in lock.read_text()
        assert '--hash=sha256:' in lock.read_text()
        assert '--require-hashes' in command and '--only-binary=:all:' in command
    assert installer[1][-1] == '/local/reviewed.whl'
    assert installer[2][-1] == 'import evlet, uvicorn, packaging'
    assert (target / '.venv/evlet-project.json').is_file()


def test_setup_bootstrap_failure_removes_only_its_new_project(tmp_path, installer, monkeypatch):
    def failed(command, **kwargs):
        installer.append(command)
        return subprocess.CompletedProcess(command, 1, stdout='', stderr='hash mismatch')

    monkeypatch.setattr(setting.subprocess, 'run', failed)
    sibling = tmp_path / 'existing'
    sibling.mkdir()
    (sibling / 'keep').write_text('untouched')
    with pytest.raises(RuntimeError, match='Project installer preparation failed: hash mismatch'):
        setting.setup(tmp_path / 'project', framework='/local/reviewed.whl')
    assert len(installer) == 1 and '--require-hashes' in installer[0]
    assert not (tmp_path / 'project').exists()
    assert (sibling / 'keep').read_text() == 'untouched'
