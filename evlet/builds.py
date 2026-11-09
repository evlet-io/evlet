"""Trusted, deterministic application discovery and immutable source generations."""

from dataclasses import replace
from importlib import import_module, metadata
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

from .models import Model, Registry
from .permissions import Policy, Rule

VERSION = 1
AREAS = ('models', 'reports', 'services', 'migrations', 'workspaces')


def compiler_fingerprint():
    digest = hashlib.sha256()
    root = Path(__file__).parent
    for path in sorted(root.rglob('*')):
        if '__pycache__' not in path.parts and path.suffix in ('.py', '.tpl', '.js', '.css', '.html'):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def module_names(package):
    names = [package.name]
    for path in package.source_files():
        if path.suffix != '.py' or not path.is_relative_to(package.source_root):
            continue
        relative = path.relative_to(package.source_root)
        parts = list(relative.with_suffix('').parts)
        # An area is a package (models/) or, for a small app, a single module (models.py).
        if parts[0] not in AREAS:
            continue
        if parts[-1] == '__init__':
            parts.pop()
        if any(not part.isidentifier() for part in parts):
            raise ValueError(f'Unsupported source module: {relative}')
        names.append('.'.join((package.name, *parts)))
    return sorted(set(names), key=lambda name: (name.count('.'), name))


def discover(package, modules=None):
    from .instances import AppDefinition
    from .migrations import Migration
    from .forms import Report
    from .workspaces import Workspace
    models, migrations, seen, reports, endpoints, tasks, schedules, workspaces = [], [], set(), [], [], [], [], []
    for name in modules if modules is not None else module_names(package):
        module = import_module(name)
        if name == package.name + '.workspaces' or name.startswith(package.name + '.workspaces.'):
            # Workspaces keep the order they are defined in, which is the order the switcher lists them.
            # One imported from another app belongs to that app, as models and reports do, so it is not collected here.
            foreign = imported_names(package, module)
            workspaces.extend(value for key, value in vars(module).items() if isinstance(value, Workspace) and key not in foreign
                              and all(value is not other for other in workspaces))
        if name == package.name + '.services' or name.startswith(package.name + '.services.'):
            from .services import collect_service
            collected = collect_service(package, module)
            endpoints.extend(collected[0]); tasks.extend(collected[1]); schedules.extend(collected[2])
        for key, value in sorted(vars(module).items()):
            if isinstance(value, type) and issubclass(value, Model) and value is not Model and value.__module__ == name:
                if issubclass(value, Report) and value is not Report:
                    value.__resource_id__ = resource_identity(package, value)
                    reports.append(value)
                    continue
                if getattr(value, '__transient__', False): continue
                if name == package.name or name.startswith(package.name + '.models.') or name == package.name + '.models':
                    value.__resource_id__ = resource_identity(package, value)
                    value.__declaration_mode__ = True
                    models.append(value)
            if isinstance(value, Migration) and name.startswith(package.name + '.migrations') and id(value) not in seen:
                seen.add(id(value))
                migrations.append(value)
    rules = {}
    for model in models:
        declarations = getattr(model, 'permissions', {})
        if not isinstance(declarations, dict):
            raise ValueError(f'{model.__resource_id__}.permissions must map roles to Rule objects')
        for role in ('admin', 'editor', 'viewer'):
            rules[role, model] = declarations.get(role, Rule(()))
        if set(declarations) - {'admin', 'editor', 'viewer'}:
            raise ValueError(f'Unknown permission role in {model.__resource_id__}')
    return AppDefinition(models=models, reports=reports, apis=endpoints, tasks=tasks, schedules=schedules,
                         permissions=rules, migrations=sorted(migrations, key=lambda migration: migration.name), workspaces=workspaces)


def imported_names(package, module):
    """Module-level names a module imports from outside its own app (from other import NAME)."""
    import ast
    tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
    return {alias.asname or alias.name for node in tree.body if isinstance(node, ast.ImportFrom) and node.level == 0
            and node.module != package.name and not (node.module or '').startswith(package.name + '.') for alias in node.names}


def resource_identity(package, definition):
    import re
    value = vars(definition).get('resource_id', package.name + '.' + definition.__name__)
    if not isinstance(value, str) or not value.startswith(package.name + '.') or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', value):
        raise ValueError('Resource identities must belong to their declaring app')
    return value


def describe(packages):
    from .instances import load_definitions
    modules = {package.name: module_names(package) for package in packages if package.layout == 'flat'}
    definitions = load_definitions(packages, modules=modules)
    registry = Registry(*(model for _, definition in definitions for model in definition.models))
    policy = Policy(registry, rules={key: value for _, definition in definitions for key, value in definition.permissions.items()})
    from .forms import Forms, metadata as form_metadata
    from types import SimpleNamespace
    forms = Forms(SimpleNamespace(registry=registry, actions={(model, name): value for model in registry.models.values() for name, value in vars(model).items() if hasattr(value, '__action__')}),
                  [report for _, definition in definitions for report in definition.reports])
    from .documents import argument_schema
    for model in registry.models.values():
        for value in vars(model).values():
            # Actions with declared inputs are validated by their fields; others need typed signatures.
            if hasattr(value, '__action__') and value.__action__.get('inputs') is None: argument_schema(value, skip=1)
    import inspect
    from typing import get_type_hints
    endpoints = [endpoint for _, definition in definitions for endpoint in definition.apis]
    schedules = [schedule for _, definition in definitions for schedule in definition.schedules]
    if len({endpoint.name for endpoint in endpoints}) != len(endpoints): raise ValueError('Duplicate endpoint identity')
    if len({schedule.name for schedule in schedules}) != len(schedules): raise ValueError('Duplicate schedule identity')
    for endpoint in endpoints:
        registry.resolve(endpoint.permission[0])
        if endpoint.permission[1] not in ('read', 'create', 'update', 'delete'): raise ValueError('Unknown API permission')
    return {
        'version': VERSION, 'modules': modules,
        'apis': [{'name': endpoint.name, 'method': endpoint.method, 'callable': endpoint.function.__module__ + ':' + endpoint.function.__name__,
                  'inputs': {parameter.name: {'type': get_type_hints(endpoint.function)[parameter.name].__name__, 'required': parameter.default is inspect.Parameter.empty}
                             for parameter in list(inspect.signature(endpoint.function).parameters.values())[1:]},
                  'output': 'JSON value, finite numbers, maximum 1 MiB',
                  'permission': [registry.name_for(registry.resolve(endpoint.permission[0])), endpoint.permission[1]],
                  'route': '/api/_services/' + endpoint.name} for endpoint in endpoints],
        'schedules': [{'name': schedule.name, 'task': schedule.task, 'expression': getattr(schedule, 'expression', None),
                       'every': getattr(schedule, 'every', None), 'timezone': getattr(schedule, 'timezone', None)} for schedule in schedules],
        'reports': [getattr(report, '__resource_id__', report.__name__) for _, definition in definitions for report in definition.reports],
        'report_definitions': [{**form_metadata(report, name), 'autorun': report.autorun, 'permissions': report.permissions,
                                'columns': [vars(column) for column in report.columns]} for name, report in forms.reports.items()],
        'dialogs': [{**form_metadata(dialog, name), 'source': registry.name_for(model), 'resource': getattr(model, '__resource_id__', registry.name_for(model)) + '.' + name} for (model, name), dialog in forms.dialogs.items()],
        'client': registry.ui_schema(),
        'policies': [{'role': role, 'model': registry.name_for(model), 'operations': rule.operations,
                      'read_fields': rule.read_fields, 'write_fields': rule.write_fields,
                      'scope': None if rule.scope is None else rule.scope.__module__ + ':' + rule.scope.__qualname__}
                     for (role, model), rule in policy.rules.items()],
        'models': [{'resource': getattr(model, '__resource_id__', name), 'storage': name,
                    'callable': model.__module__ + ':' + model.__qualname__,
                    'fields': [field.metadata() for field in model.__fields__.values()]}
                   for name, model in registry.models.items()],
    }


def prepare(root, packages):
    """Caller must authorize execution first. Never opens an instance database."""
    from .cli.project import atomic_json
    root = Path(root).resolve()
    inputs = {'compiler': VERSION, 'compiler_sha256': compiler_fingerprint(), 'framework': metadata.version('evlet'),
              'python': list(sys.version_info[:2]),
              'apps': {package.name: package.locked() for package in packages},
              'dependencies': dependency_fingerprint(packages)}
    fingerprint = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    base = root / '.evlet'
    builds = base / 'builds'
    for path in (base, builds, builds / fingerprint):
        if path.is_symlink():
            raise ValueError('Build paths cannot be symbolic links')
    builds.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = builds / fingerprint
    if not (target / 'manifest.json').is_file():
        with tempfile.TemporaryDirectory(prefix='.build-', dir=builds) as directory:
            stage = Path(directory)
            copied = []
            for package in packages:
                destination = stage / 'sources' / package.name
                for source in package.source_files():
                    output = destination / source.relative_to(package.path)
                    output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, output)
                copied.append({'path': str(destination), 'name': package.name, 'version': package.version,
                               'title': package.title, 'layout': package.layout,
                               'dependencies': [str(item) for item in package.dependencies]})
            request = stage / 'request.json'
            request.write_text(json.dumps(copied))
            # -P keeps the caller's working directory off sys.path so local files cannot shadow modules.
            result = subprocess.run([sys.executable, '-P', '-m', 'evlet.builds', str(request), str(stage / 'manifest.json')],
                                    capture_output=True, text=True, timeout=120)
            if result.returncode:
                raise ValueError('Application build failed:\n' + result.stderr.strip())
            manifest = json.loads((stage / 'manifest.json').read_text())
            manifest['outputs'] = {str(path.relative_to(stage)): hashlib.sha256(path.read_bytes()).hexdigest()
                                   for path in sorted((stage / 'sources').rglob('*')) if path.is_file() and '__pycache__' not in path.parts}
            manifest.update(fingerprint=fingerprint, inputs=inputs)
            atomic_json(stage / 'manifest.json', manifest)
            request.unlink()
            if {package.name: package.locked() for package in packages} != inputs['apps']:
                raise ValueError('App sources changed during build; retry with stable sources')
            try:
                os.rename(stage, target)
            except OSError:
                # Another process published the same generation first (EEXIST or ENOTEMPTY).
                if not (target / 'manifest.json').is_file():
                    raise
    manifest = json.loads((target / 'manifest.json').read_text())
    if manifest.get('inputs') != inputs or manifest.get('version') != VERSION:
        raise ValueError('Build generation is incompatible or damaged')
    for name, digest in manifest.get('outputs', {}).items():
        file = target / name
        if not file.resolve().is_relative_to(target) or file.is_symlink() or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            raise ValueError('Build generation content differs from its manifest: ' + name)
    pinned = tuple(replace(package, path=target / 'sources' / package.name) for package in packages)
    atomic_json(base / 'current.json', {'fingerprint': fingerprint})
    return pinned, manifest


def dependency_fingerprint(packages):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    apps = {canonicalize_name(package.name) for package in packages}
    result, visited = {}, set()
    def visit(requirement):
        name = canonicalize_name(requirement.name)
        key = (name, tuple(sorted(requirement.extras)))
        if name in apps or key in visited: return
        visited.add(key)
        distribution = metadata.distribution(requirement.name)
        result[name] = {'version': distribution.version,
                        'metadata_sha256': hashlib.sha256((distribution.read_text('METADATA') or '').encode()).hexdigest(),
                        'source_sha256': hashlib.sha256((distribution.read_text('direct_url.json') or '').encode()).hexdigest()}
        for value in distribution.requires or ():
            child = Requirement(value)
            if not child.marker or any(child.marker.evaluate({'extra': extra}) for extra in ('', *requirement.extras)):
                visit(child)
    for package in packages:
        for requirement in package.dependencies:
            if not requirement.marker or requirement.marker.evaluate(): visit(requirement)
    return result


if __name__ == '__main__':
    from packaging.requirements import Requirement
    from .packages import Package
    request, output = map(Path, sys.argv[1:])
    packages = tuple(Package(**{**item, 'path': Path(item['path']),
                               'dependencies': tuple(Requirement(value) for value in item['dependencies'])})
                     for item in json.loads(request.read_text()))
    output.write_text(json.dumps(describe(packages), sort_keys=True))
