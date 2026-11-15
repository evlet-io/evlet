"""Project environment creation order and failure isolation, without network installs."""

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


def test_setup_installs_the_framework_with_the_environment_pip(tmp_path, installer):
    target = setting.setup(tmp_path / 'project', framework='/local/reviewed.whl')
    python = str(setting.python_in(target))
    # The interpreter's bundled pip installs the framework; setup never replaces pip itself.
    assert installer[0] == [python, '-m', 'pip', 'install', '--disable-pip-version-check', '/local/reviewed.whl']
    assert installer[1][-1] == 'import evlet, uvicorn, packaging'
    assert len(installer) == 2
    assert (target / '.venv/evlet-project.json').is_file()


def test_setup_install_failure_removes_only_its_new_project(tmp_path, installer, monkeypatch):
    def failed(command, **kwargs):
        installer.append(command)
        return subprocess.CompletedProcess(command, 1, stdout='', stderr='no matching distribution')

    monkeypatch.setattr(setting.subprocess, 'run', failed)
    sibling = tmp_path / 'existing'
    sibling.mkdir()
    (sibling / 'keep').write_text('untouched')
    with pytest.raises(RuntimeError, match='Project environment preparation failed: no matching distribution'):
        setting.setup(tmp_path / 'project', framework='/local/reviewed.whl')
    assert len(installer) == 1
    assert not (tmp_path / 'project').exists()
    assert (sibling / 'keep').read_text() == 'untouched'
