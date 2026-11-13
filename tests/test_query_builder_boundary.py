"""The query builder boundary (docs/database-adapters.md): SQL text lives in the dialect only.

Framework code builds statements with evlet/qb.py and runs them with connection.run(). Only the SQLite dialect
(evlet/sqlite.py, evlet/writes.py and the Database class in evlet/database.py) calls execute() with SQL text.
Modules not yet moved onto the query builder are listed in NOT_YET_MOVED with the number of raw calls they have
today: the number may only go down, and a module leaves the list when it reaches zero. A module that is not
listed may not call execute() at all, so new framework code uses the query builder.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'evlet'

# The dialect: SQLite SQL text is allowed here.
DIALECT = {'sqlite.py', 'writes.py'}

# Raw execute() calls left per module, moved in the follow-up pull requests listed in docs/database-adapters.md.
NOT_YET_MOVED = {
    'assistants.py': 42, 'business.py': 17, 'connectors.py': 11, 
    'corrections.py': 9, 'currencies.py': 4, 'devlink.py': 9, 'effects.py': 7, 'eva.py': 109,
    'events.py': 11, 'forms.py': 3, 'framework.py': 7, 'jobs.py': 67, 'mailboxes.py': 28, 'measures.py': 4, 'mentions.py': 15,
    'object_storage.py': 6, 'printing.py': 7, 'services.py': 3, 'slips.py': 3,
    'storage.py': 10, 'tools.py': 19, 
    # Instance tools that open SQLite files directly (backups, restores, the instance catalog).
    'instances.py': 14, 'tenancy.py': 18, 'cli/backup.py': 7, 'cli/install.py': 4, 'cli/journal.py': 5, 'cli/main.py': 1,
    'cli/serve.py': 2, 'cli/site.py': 2,
}


def raw_calls(path):
    """The execute(), executemany() and executescript() calls of a module, outside class Database in database.py."""
    found = 0

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.classes = []

        def visit_ClassDef(self, node):
            self.classes.append(node.name)
            self.generic_visit(node)
            self.classes.pop()

        def visit_Call(self, node):
            nonlocal found
            if isinstance(node.func, ast.Attribute) and node.func.attr in ('execute', 'executemany', 'executescript'):
                if not (path.name == 'database.py' and 'Database' in self.classes):
                    found += 1
            self.generic_visit(node)

    Visitor().visit(ast.parse(path.read_text()))
    return found


def test_sql_text_stays_in_the_dialect():
    problems = []
    for path in sorted(ROOT.rglob('*.py')):
        name = path.relative_to(ROOT).as_posix()
        if name in DIALECT:
            continue
        count, allowed = raw_calls(path), NOT_YET_MOVED.get(name, 0)
        if count > allowed:
            problems.append(f'{name}: {count} raw execute() calls, at most {allowed}; build statements with evlet/qb.py')
        elif count < allowed:
            problems.append(f'{name}: {count} raw execute() calls; lower NOT_YET_MOVED to {count}' if count
                            else f'{name}: no raw execute() calls left; remove it from NOT_YET_MOVED')
    assert not problems, '\n'.join(problems)
