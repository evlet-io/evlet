"""Package layers: a module imports only from its own layer or the layers below it (docs/architecture.md).

evlet/ui/, evlet/cli/ and evlet/resources/ sit above all eight layers and are not checked here.
"""

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / 'evlet'

# Lowest first.
LAYERS = {
    'core': 'adapter qb identity fields models declarations expressions calc reactivity currencies richtext naming indexes '
            'query audit sections periods',
    'access': 'auth permissions',
    'data': 'database sqlite migrations ledger writes storage object_storage secrets sequences images',
    'engine': 'documents statemanager guards effects journal measures unsaved mentions meaning',
    'web': 'app runtime routing business forms slips workspaces services events jobs framework devlink discovery',
    'admin': 'controls controls_export controls_system customization settings printing',
    'ai': 'eva tools mcp connector connectors assistants mailboxes',
    'site': 'instances packages builds deployment tenancy serverless',
}
LAYER = {module: number for number, modules in enumerate(LAYERS.values()) for module in modules.split()}
NAME = dict(enumerate(LAYERS))

# Function-level imports that still point upward, as (importing module, imported module).
# This list only shrinks: remove an entry when its import is gone.
ALLOWED_UPWARD = {
    # core
    ('currencies', 'auth'),
    ('declarations', 'auth'),
    ('naming', 'migrations'),
    ('periods', 'controls'),
    ('sections', 'documents'),
    ('sections', 'permissions'),
    # access
    ('auth', 'controls'),
    ('auth', 'migrations'),
    ('auth', 'routing'),
    # data
    ('database', 'documents'),
    ('database', 'journal'),
    ('database', 'measures'),
    ('images', 'business'),
    ('migrations', 'controls'),
    ('migrations', 'controls_export'),
    ('migrations', 'documents'),
    ('migrations', 'effects'),
    ('migrations', 'framework'),
    ('migrations', 'journal'),
    ('migrations', 'statemanager'),
    ('sequences', 'documents'),
    ('sequences', 'journal'),
    # engine
    ('documents', 'jobs'),
    ('documents', 'printing'),
    ('mentions', 'connectors'),
    ('unsaved', 'controls'),
    ('unsaved', 'jobs'),
    # web
    ('app', 'eva'),
    ('app', 'instances'),
    ('app', 'printing'),
    ('business', 'assistants'),
    ('business', 'connectors'),
    ('business', 'controls'),
    ('business', 'mailboxes'),
    ('business', 'printing'),
    ('business', 'settings'),
    ('business', 'tools'),
    ('devlink', 'controls'),
    ('framework', 'controls'),
    ('framework', 'tools'),
    # admin
    ('controls', 'connectors'),
    ('controls', 'eva'),
    ('controls', 'tools'),
    ('controls_system', 'instances'),
    ('controls_system', 'packages'),
}


def layered_modules():
    return sorted(path.stem for path in PACKAGE.glob('*.py') if path.stem not in ('__init__', '__main__'))


def imported_modules(node):
    """The evlet modules one import statement names, for a module at the package root."""
    if isinstance(node, ast.ImportFrom):
        if node.level == 1 or (node.level == 0 and node.module and node.module.split('.')[0] == 'evlet'):
            parts = (node.module or '').split('.')[node.level == 0:]
            if parts and parts[0]:
                return [parts[0]]
            return [alias.name for alias in node.names]  # from . import qb, models
        return []
    if isinstance(node, ast.Import):
        return [alias.name.split('.')[1] for alias in node.names if alias.name.startswith('evlet.')]
    return []


def imports(module):
    """Yield (imported module, line, inside a function) for every evlet import in a layered module."""
    tree = ast.parse((PACKAGE / f'{module}.py').read_text(), filename=f'evlet/{module}.py')

    def walk(node, in_function):
        for child in ast.iter_child_nodes(node):
            nested = in_function or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            for target in imported_modules(child):
                if target in LAYER:
                    yield target, child.lineno, in_function
            yield from walk(child, nested)

    yield from walk(tree, False)


def upward_imports():
    for module in layered_modules():
        for target, line, in_function in imports(module):
            if module in LAYER and LAYER[target] > LAYER[module]:
                yield module, target, line, in_function


def test_every_module_has_a_layer():
    modules = layered_modules()
    missing = [module for module in modules if module not in LAYER]
    assert not missing, f'Place each new module in a layer in tests/test_layers.py LAYERS: {missing}'
    gone = sorted(set(LAYER) - set(modules))
    assert not gone, f'Remove modules that no longer exist from tests/test_layers.py LAYERS: {gone}'


def test_no_module_level_import_points_upward():
    found = [f'evlet/{module}.py:{line} ({NAME[LAYER[module]]}) imports {target} ({NAME[LAYER[target]]})'
             for module, target, line, in_function in upward_imports() if not in_function]
    assert not found, 'Module-level imports must point to the same or a lower layer:\n' + '\n'.join(found)


def test_function_level_upward_imports_are_allowed_explicitly():
    found = [(module, target, line) for module, target, line, in_function in upward_imports() if in_function]
    new = [f'evlet/{module}.py:{line} ({NAME[LAYER[module]]}) imports {target} ({NAME[LAYER[target]]})'
           for module, target, line in found if (module, target) not in ALLOWED_UPWARD]
    assert not new, 'Imports must point to the same or a lower layer; move the code instead:\n' + '\n'.join(new)


def test_upward_allowlist_only_shrinks():
    stale = sorted(ALLOWED_UPWARD - {(module, target) for module, target, _, _ in upward_imports()})
    assert not stale, f'These upward imports are gone; delete them from ALLOWED_UPWARD in tests/test_layers.py: {stale}'


def test_the_guard_sees_function_and_module_level_imports():
    # A sanity check on the parser itself, against imports that exist today.
    database = {(target, in_function) for target, _, in_function in imports('database')}
    assert ('images', False) in database  # from . import images, ledger, periods
    assert ('qb', False) in database
    assert any(target == 'documents' and in_function for target, in_function in database)
