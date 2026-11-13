"""The query builder boundary (docs/database-adapters.md): SQL text lives in the dialect only.

Framework code builds statements with evlet/qb.py and runs them with connection.run(), and changes the schema
with the connection's named methods. Only the SQLite dialect (evlet/sqlite.py, evlet/writes.py and the Database
class in evlet/database.py) writes SQL text. This test fails on SQL text anywhere else in evlet/: a string that
reads as an SQL statement, or an execute(), executemany() or executescript() call given text.

The instance tools that open SQLite files themselves (backups, restores, the instance catalog) are listed in
INSTANCE_TOOLS with the SQL they hold today; deciding which of them stay SQLite tools is the next step of the
plan. Their numbers may only go down.
"""

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / 'evlet'

# The dialect: SQL text is allowed here (in database.py, only inside class Database).
DIALECT = {'sqlite.py', 'writes.py'}

# SQL found per instance tool: statements in strings plus execute() calls given text.
INSTANCE_TOOLS = {
    'instances.py': 14, 'tenancy.py': 19, 'cli/backup.py': 7, 'cli/install.py': 4, 'cli/journal.py': 5, 'cli/main.py': 1,
    'cli/serve.py': 2, 'cli/site.py': 2,
}

# The start of an SQL statement, keywords in capitals as Evlet writes SQL.
STATEMENT = re.compile(r'\s*(SELECT\s|INSERT\s+(OR\s+\w+\s+)?INTO\s|UPDATE\s+\S+\s+SET\s|DELETE\s+FROM\s|'
                       r'(CREATE|DROP)\s+(UNIQUE\s+)?(TABLE|INDEX|TRIGGER|VIEW)\s|ALTER\s+TABLE\s|PRAGMA\s|'
                       r'BEGIN(\s|$)|COMMIT$|ROLLBACK(\s|$)|SAVEPOINT\s|RELEASE\s|WITH\s+RECURSIVE\s|ATTACH\s|VACUUM)')
EXECUTE = {'execute', 'executemany', 'executescript'}


def _text(node):
    """The leading literal text of a string expression (a constant, an f-string or a + of them), else None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return ''.join(part.value if isinstance(part, ast.Constant) else '?' for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _text(node.left)
    return None


def sql_text(path):
    """Where a module writes SQL text: (line, what) pairs, outside class Database in database.py."""
    found = []
    docstrings = set()
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))

    class Visitor(ast.NodeVisitor):
        def __init__(self):
            self.classes = []

        def allowed(self):
            return path.name == 'database.py' and 'Database' in self.classes

        def visit_ClassDef(self, node):
            self.classes.append(node.name)
            self.generic_visit(node)
            self.classes.pop()

        def visit_Call(self, node):
            if (isinstance(node.func, ast.Attribute) and node.func.attr in EXECUTE and node.args
                    and _text(node.args[0]) is not None and not self.allowed()):
                found.append((node.lineno, f'{node.func.attr}() given SQL text'))
                return  # its text is reported once
            self.generic_visit(node)

        def visit_JoinedStr(self, node):
            self.check(node)

        def visit_Constant(self, node):
            if id(node) not in docstrings:
                self.check(node)

        def check(self, node):
            text = _text(node)
            if text is not None and STATEMENT.match(text) and not self.allowed():
                found.append((node.lineno, repr(text[:40])))

    Visitor().visit(tree)
    return found


def test_sql_text_stays_in_the_dialect():
    problems = []
    for path in sorted(ROOT.rglob('*.py')):
        name = path.relative_to(ROOT).as_posix()
        if name in DIALECT:
            continue
        found, allowed = sql_text(path), INSTANCE_TOOLS.get(name, 0)
        if len(found) > allowed:
            where = ', '.join(f'line {line} {what}' for line, what in found)
            problems.append(f'{name}: SQL text outside the dialect ({where}); build statements with evlet/qb.py')
        elif len(found) < allowed:
            problems.append(f'{name}: {len(found)} SQL texts left; lower INSTANCE_TOOLS to {len(found)}' if found
                            else f'{name}: no SQL text left; remove it from INSTANCE_TOOLS')
    assert not problems, '\n'.join(problems)


def test_the_check_finds_sql_text(tmp_path):
    sample = tmp_path / 'sample.py'
    sample.write_text('def run(connection, table, tools):\n'
                      '    """SELECT in a docstring is prose."""\n'
                      "    connection.execute(f'SELECT * FROM {table}')\n"
                      "    query = 'DELETE FROM evlet_jobs WHERE id=?'\n"
                      "    connection.executescript('PRAGMA foreign_keys=ON;')\n"
                      "    tools.execute(table, 'Update the order')\n"
                      "    message = 'Select a record first'\n")
    assert [line for line, _ in sql_text(sample)] == [3, 4, 5]
