"""Batch 4 developer experience: evlet serve logs app paths (DX-12), the formula language is explicit (DX-13), and
F and evlet.essentials give a first app a short import (DX-14)."""

import io
import logging
import warnings
from pathlib import Path

import pytest

import evlet
import evlet.essentials
from evlet import Decimal, Model, Registry
from evlet.builds import SOURCES
from evlet.calc import CalculationLanguageWarning, F
from evlet.cli import serve as serving
from evlet.cli.install import install

from test_cli_lifecycle import environment_stub, project, runtime  # noqa: F401


@pytest.fixture
def record_factory():
    previous = logging.getLogRecordFactory()
    yield
    logging.setLogRecordFactory(previous)


def logged(logger, raise_from):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        try:
            raise_from()
        except ZeroDivisionError:
            logger.exception('Unhandled request failure')
    finally:
        logger.removeHandler(handler)
    return stream.getvalue()


def test_serve_logs_tracebacks_with_the_app_source_path(project, monkeypatch, record_factory):  # noqa: F811
    install(project, 'acme', app_name='notes', trust=True)
    monkeypatch.setattr(serving, 'run_server', lambda application, **options: None)
    serving.serve(project, 'acme')
    assert getattr(logging.getLogRecordFactory(), 'evlet_source_paths', False)
    runtime(project).database.close()  # imports the app from its build generation, as the server does
    copy, origin = next((copy, origin) for copy, origin in SOURCES.items() if Path(origin).is_relative_to(project / 'apps'))
    assert '.evlet' in Path(copy).parts and copy != origin
    namespace = {}
    exec(compile('def handler():\n    return 1 / 0\n', str(Path(copy) / 'models' / 'invoice.py'), 'exec'), namespace)
    text = logged(logging.getLogger('evlet.test.dx12'), namespace['handler'])
    assert str(Path(origin) / 'models' / 'invoice.py') in text and 'ZeroDivisionError' in text
    assert copy not in text


def test_lifecycle_error_message_names_the_app_path(record_factory):
    SOURCES['/tmp/x/.evlet/builds/abc/sources/notes'] = '/tmp/x/apps/notes'
    try:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger(serving.__name__)
        logger.addHandler(handler)
        try:
            serving.ManagedRuntime(lambda: None).report(ValueError('Bad model in /tmp/x/.evlet/builds/abc/sources/notes/models.py'))
        finally:
            logger.removeHandler(handler)
        assert stream.getvalue().strip() == 'Bad model in /tmp/x/apps/notes/models.py'
    finally:
        SOURCES.pop('/tmp/x/.evlet/builds/abc/sources/notes')


def test_implicit_finite_language_warns_once_naming_the_model():
    class DxImplicitLine(Model):
        qty = Decimal(scale=2)
        total = Decimal(scale=2, compute=F('qty') * 2)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter('always')
        Registry(DxImplicitLine)
        Registry(DxImplicitLine)
    found = [item for item in caught if issubclass(item.category, CalculationLanguageWarning)]
    assert len(found) == 1
    message = str(found[0].message)
    assert 'DxImplicitLine' in message and 'total' in message and 'calculation_language = "finite-v1"' in message
    assert issubclass(CalculationLanguageWarning, UserWarning)


def test_explicit_language_and_string_formulas_stay_quiet():
    class DxExplicitLine(Model):
        qty = Decimal(scale=2)
        total = Decimal(scale=2, compute=F('qty') * 2)

        class Meta:
            calculation_language = 'finite-v1'

    class DxLegacyLine(Model):
        qty = Decimal(scale=2)
        total = Decimal(scale=2, compute='qty * 2')

    with warnings.catch_warnings():
        warnings.simplefilter('error', CalculationLanguageWarning)
        registry = Registry(DxExplicitLine, DxLegacyLine)
    assert registry.plan_for(DxExplicitLine) is not None and registry.plan_for(DxLegacyLine) is None


def test_f_is_a_root_export_and_essentials_reexports_root_objects():
    assert evlet.F is F and 'F' in evlet.__all__
    names = evlet.essentials.__all__
    assert set(names) <= set(evlet.__all__)
    assert all(getattr(evlet.essentials, name) is getattr(evlet, name) for name in names)
    assert {'Model', 'Child', 'Table', 'Link', 'State', 'Rule', 'validate', 'action', 'ValidationError', 'Report',
            'Column', 'api', 'cron', 'F'} <= set(names)
    assert len(names) < 30
    namespace = {}
    exec('from evlet.essentials import *', namespace)
    assert set(namespace) - {'__builtins__'} == set(names)


def test_essentials_table_in_app_layout_lists_the_module():
    text = (Path(__file__).resolve().parents[1] / 'docs' / 'app-layout.md').read_text()
    section = text.split('## The essentials', 1)[1].split('\n## ', 1)[0]
    for name in evlet.essentials.__all__:
        assert f'`{name}`' in section, name



def test_cli_errors_name_the_apps_own_files(monkeypatch, capsys):
    import importlib
    import pytest
    from evlet import builds
    cli = importlib.import_module('evlet.cli.main')
    monkeypatch.setitem(builds.SOURCES, '/p/.evlet/builds/abc/notes', '/p/apps/notes')
    def broken(args, context):
        raise ValueError('/p/.evlet/builds/abc/notes/models/item.py:3: bad field')
    entry = cli.command_entry
    monkeypatch.setattr(cli, 'command_entry', lambda name: (entry(name)[0], broken))
    with pytest.raises(SystemExit):
        cli.main(['docs'])
    assert '/p/apps/notes/models/item.py:3: bad field' in capsys.readouterr().err
